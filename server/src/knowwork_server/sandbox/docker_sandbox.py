"""SessionSandbox 的 Docker 实现，外加给沙箱工具用的工作区文件操作。

执行身份照抄 WeKnora：容器的 PID 1 以 root 运行，命令与文件操作默认也是 root（exec_user 可改成 uid[:gid]，
单次调用也可指定 user，例如镜像里保留的 uid 1000）。一个沙箱只属于一个会话，容器里没有第二个需要内核替它
隔开的账号；跨会话、跨租户与宿主机的隔离在容器边界，不靠工作目录的文件权限。

- 读文件、列目录、收产物都经 exec 在容器里跑（带执行账号、超时与活动标记）；
- 写入内容（附件、宿主文件工具的写入）经 Docker 的 archive 接口放进去：docker-py 接管的 exec 连接在 TLS 上
  没法可靠地半关闭写端，stdin 送不完整（WeKnora 用 Go 的 CloseWrite 走 exec stdin）。archive 由 daemon
  以 root 身份解包，与默认执行账号相同，不改变权限口径；写之前先用 exec 以执行账号确认目标可写；
- 产物是模型用 present 交付的文件，只收普通文件（最后一级不跟随符号链接）。
"""

import posixpath
import secrets
from dataclasses import dataclass

from .containers import ContainerManager, SandboxKey
from .contract import INPUT_DIR, ExecResult, SandboxError, SandboxFile, SandboxTooLarge
from .docker_engine import DockerEngine
from .execution import ExecRequest, RawExec, run
from .paths import WORKSPACE_DIR, check_name, resolve, resolve_writable
from .scripts import CONVERT_TO_PDF, FILE_ERRORS, LIST_DIR, PREPARE_WRITE, READ_DELIVERED, READ_FILE, STAT_PATH
from .tarball import Entry, build

INTERNAL_TIMEOUT_SECONDS = 30.0
LIST_OUTPUT_BYTES = 1024 * 1024
MAX_EXEC_OUTPUT = 16 * 1024 * 1024
NOT_STARTED = "这个会话还没有沙箱"
# 沙箱镜像里原生 LibreOffice 的封装脚本（见 deploy/docker/sandbox.Dockerfile，照搬 KnowFlow）
SOFFICE = "/opt/knowwork/office/soffice.py"
CONVERTIBLE = frozenset({"doc", "docx", "odt", "rtf", "ppt", "pptx", "odp"})


@dataclass(frozen=True)
class DirEntry:
    name: str
    kind: str  # f 普通文件 / d 目录 / l 符号链接 / o 其他
    size: int


@dataclass(frozen=True)
class PathStat:
    kind: str  # f 普通文件 / d 目录 / l 符号链接 / o 其他
    size: int
    version: str  # 修改时间（纳秒）与 inode、字节数拼成，内容一变就变


@dataclass(frozen=True)
class FileChunk:
    path: str
    size: int  # 文件总字节数
    offset: int
    data: bytes


def _parse_listing(raw: bytes) -> list[DirEntry]:
    fields = raw.split(b"\x00")
    entries = []
    for index in range(0, len(fields) - 2, 3):
        kind, size, name = fields[index:index + 3]
        try:
            entries.append(DirEntry(name=name.decode("utf-8", errors="replace"), kind=kind.decode(),
                                    size=int(size or 0)))
        except ValueError:
            continue
    return sorted(entries, key=lambda entry: entry.name)


def _parse_stat(raw: bytes) -> PathStat:
    kind_text, size, mtime, inode = raw.decode("utf-8", errors="replace").strip().split("|")
    kind = ("f" if kind_text.startswith("regular") else "d" if kind_text == "directory"
            else "l" if kind_text == "symbolic link" else "o")
    return PathStat(kind=kind, size=int(size), version=f"{mtime}:{inode}:{size}")


def _env_argv(env: dict[str, str] | None, command: list[str]) -> list[str]:
    """环境变量经 env(1) 带进去：Docker 的 exec 接口按调用给环境变量，但 docker-py 的低层封装没有暴露。"""
    if not env:
        return command
    for key, value in env.items():
        if not key or "=" in key or "\x00" in key or "\x00" in value:
            raise SandboxError("环境变量不合法")
    return ["env", *(f"{key}={value}" for key, value in env.items()), *command]


# 文件脚本的退出码 → 给远程文件系统用的错误码（宿主据此换成 DSH 的 FsError 码，不去解析提示语）
FILE_ERROR_CODES = {3: "not_found", 4: "is_directory", 5: "not_regular_file", 6: "permission_denied",
                    7: "permission_denied", 9: "too_large"}


class SandboxFileError(SandboxError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def _file_error(result: RawExec) -> SandboxError:
    if result.exit_code == 9:
        return SandboxTooLarge(FILE_ERRORS[9])
    return SandboxFileError(FILE_ERRORS.get(result.exit_code, "文件操作失败"),
                            FILE_ERROR_CODES.get(result.exit_code, "io_error"))


def owner_of(user: str) -> tuple[int, int]:
    """archive 写入的文件属主跟执行账号一致：root 为 0:0，数字账号按 uid[:gid]。"""
    if user == "root":
        return 0, 0
    uid, _, gid = user.partition(":")
    return int(uid), int(gid or uid)


def _check_command(command: list[str]) -> None:
    if not command or any(not isinstance(arg, str) or "\x00" in arg for arg in command):
        raise SandboxError("命令不合法")


class DockerSessionSandbox:
    def __init__(self, engine: DockerEngine, containers: ContainerManager, *, user: str, max_input_bytes: int,
                 max_write_bytes: int, max_timeout_seconds: float) -> None:
        self._engine = engine
        self._containers = containers
        self._user = user
        self._owner = owner_of(user)
        self._max_input = max_input_bytes
        self._max_write = max_write_bytes
        self._max_timeout = max_timeout_seconds

    @property
    def max_write_bytes(self) -> int:
        return self._max_write

    async def _run(self, container_id: str, argv: list[str], *, timeout: float = INTERNAL_TIMEOUT_SECONDS,
                   cap: int = LIST_OUTPUT_BYTES, workdir: str = WORKSPACE_DIR, user: str | None = None) -> RawExec:
        request = ExecRequest(argv=tuple(argv), user=user or self._user, workdir=workdir, timeout_seconds=timeout,
                              max_output_bytes=cap)
        return await run(self._engine, container_id, request)

    async def _script(self, workspace_id: str, session_id: str, script: str, args: list[str], *,
                      create: bool = True, cap: int = LIST_OUTPUT_BYTES) -> RawExec | None:
        async with self._containers.using(SandboxKey.of(workspace_id, session_id), create=create) as container_id:
            if container_id is None:
                return None
            return await self._run(container_id, ["sh", "-c", script, "kw", *args], cap=cap)

    # -- 合同 ---------------------------------------------------------------

    async def put_input(self, workspace_id: str, session_id: str, name: str, data: bytes) -> SandboxFile:
        check_name(name)
        if len(data) > self._max_input:
            raise SandboxTooLarge(f"附件 {name} 超出沙箱单文件上限")
        uid, gid = self._owner
        # 0444：附件由系统放入，约定只读（root 不受文件权限约束，这不是隔离手段）
        tar = build([Entry(f"{INPUT_DIR.lstrip('/')}/{name}", uid, gid, 0o444, data)])
        async with self._containers.using(SandboxKey.of(workspace_id, session_id)) as container_id:
            await self._engine.put_archive(str(container_id), "/", tar)
        return SandboxFile(name=name, size=len(data))

    async def read_delivered(self, workspace_id: str, session_id: str, path: str, *, max_bytes: int) -> bytes:
        """读一个交付的文件（模型用 present 声明的）：/workspace 下的普通文件，最后一级不跟随符号链接。"""
        target = resolve(path)
        if max_bytes <= 0:
            raise SandboxTooLarge(FILE_ERRORS[9])
        result = await self._script(workspace_id, session_id, READ_DELIVERED, [target, str(max_bytes)], create=False,
                                    cap=max_bytes)
        if result is None:
            raise SandboxError(NOT_STARTED)
        if result.exit_code != 0:
            raise _file_error(result)
        if result.truncated:
            raise SandboxTooLarge(FILE_ERRORS[9])
        return result.stdout

    async def exec(self, workspace_id: str, session_id: str, command: list[str], *, timeout_seconds: float,
                   max_output_bytes: int, user: str | None = None, workdir: str = WORKSPACE_DIR,
                   env: dict[str, str] | None = None) -> ExecResult:
        """user 留空用配置的执行账号（默认 root）；可按调用指定，例如 "1000:1000"。workdir 须在 /workspace 下。"""
        workdir = resolve(workdir)
        command = _env_argv(env, command)
        _check_command(command)
        if not 0 < timeout_seconds <= self._max_timeout:
            raise SandboxError(f"超时时间须在 0 到 {self._max_timeout:g} 秒之间")
        if not 0 < max_output_bytes <= MAX_EXEC_OUTPUT:
            raise SandboxError("输出上限不合法")
        if user is not None and (not isinstance(user, str) or not user.strip() or "\x00" in user):
            raise SandboxError("执行账号不合法")
        async with self._containers.using(SandboxKey.of(workspace_id, session_id)) as container_id:
            result = await self._run(str(container_id), list(command), timeout=timeout_seconds,
                                     cap=max_output_bytes, user=user, workdir=workdir)
        return ExecResult(exit_code=result.exit_code, stdout=result.stdout.decode("utf-8", errors="replace"),
                          stderr=result.stderr.decode("utf-8", errors="replace"), timed_out=result.timed_out,
                          truncated=result.truncated)

    async def convert_to_pdf(self, workspace_id: str, session_id: str, extension: str, data: bytes, *,
                             max_bytes: int, timeout_seconds: float) -> bytes:
        """在会话容器里用原生 LibreOffice 把一份 Office 文档转成 PDF（预览用）。文件放在容器的私有临时目录，
        不进工作区，转完即删。
        解析外来文档的风险留在断网、无凭证的沙箱里，不进业务服务与宿主。"""
        if extension not in CONVERTIBLE:
            raise SandboxError("这种文件不能转换成 PDF")
        if len(data) > self._max_input:
            raise SandboxTooLarge("文件超出转换上限")
        directory = f"/tmp/kw-convert-{secrets.token_hex(8)}"
        tar = build([Entry(f"{directory.lstrip('/')}/in.{extension}", 0, 0, 0o600, data)])
        async with self._containers.using(SandboxKey.of(workspace_id, session_id)) as container_id:
            await self._engine.put_archive(str(container_id), "/", tar)
            result = await self._run(str(container_id), ["sh", "-c", CONVERT_TO_PDF, "kw", directory, SOFFICE,
                                                         extension, str(max_bytes)],
                                     timeout=timeout_seconds, cap=max_bytes, workdir="/", user="root")
        if result.timed_out:
            raise SandboxError("文档转换超时")
        if result.exit_code == 9 or result.truncated:
            raise SandboxTooLarge("转换出的 PDF 超出上限")
        if result.exit_code == 11:
            raise SandboxError("沙箱内存不足，文档转换被系统终止，请让管理员调高沙箱内存上限")
        if result.exit_code != 0 or not result.stdout.startswith(b"%PDF-"):
            detail = result.stderr.decode("utf-8", errors="replace")[-300:]
            raise SandboxError(f"文档转换失败（{detail.strip() or '没有输出'}）")
        return result.stdout

    async def destroy(self, workspace_id: str, session_id: str) -> None:
        await self._containers.destroy(SandboxKey.of(workspace_id, session_id))

    # -- 工作区文件（沙箱工具用） --------------------------------------------

    async def stat_path(self, workspace_id: str, session_id: str, path: str, *, follow: bool) -> PathStat | None:
        """不存在返回 None。还没有容器时不为此建一个：工作区根目录当作空目录，其余路径都不存在。"""
        target = resolve(path)
        result = await self._script(workspace_id, session_id, STAT_PATH, [target, "1" if follow else "0"],
                                    create=False)
        if result is None:
            return PathStat(kind="d", size=0, version="empty") if target == WORKSPACE_DIR else None
        if result.exit_code == 3:
            return None
        if result.exit_code != 0:
            raise _file_error(result)
        try:
            return _parse_stat(result.stdout)
        except ValueError as error:
            raise SandboxError("文件操作失败") from error

    async def list_dir(self, workspace_id: str, session_id: str, path: str) -> list[DirEntry]:
        target = resolve(path)
        result = await self._script(workspace_id, session_id, LIST_DIR, [target])
        if result is None or result.exit_code != 0:
            raise SandboxError("目录不存在或无法读取")
        return _parse_listing(result.stdout)

    async def read_file(self, workspace_id: str, session_id: str, path: str, *, offset: int,
                        limit: int) -> FileChunk:
        target = resolve(path)
        if offset < 0 or limit <= 0:
            raise SandboxError("读取范围不合法")
        result = await self._script(workspace_id, session_id, READ_FILE, [target, str(offset), str(limit)],
                                    cap=limit + 32)
        if result is None or result.exit_code != 0:
            raise _file_error(result) if result else SandboxError(NOT_STARTED)
        head, _, data = result.stdout.partition(b"\n")
        try:
            size = int(head)
        except ValueError as error:
            raise SandboxError("文件操作失败") from error
        return FileChunk(path=target, size=size, offset=offset, data=data[:limit])

    async def write_file(self, workspace_id: str, session_id: str, path: str, data: bytes) -> int:
        target = resolve_writable(path)
        if len(data) > self._max_write:
            raise SandboxTooLarge("写入内容超出上限")
        parent, name = posixpath.split(target)
        uid, gid = self._owner
        tar = build([Entry(name, uid, gid, 0o644, data)])
        async with self._containers.using(SandboxKey.of(workspace_id, session_id)) as container_id:
            prepared = await self._run(str(container_id), ["sh", "-c", PREPARE_WRITE, "kw", target])
            if prepared.exit_code != 0:
                raise _file_error(prepared)
            await self._engine.put_archive(str(container_id), parent, tar)
        return len(data)
