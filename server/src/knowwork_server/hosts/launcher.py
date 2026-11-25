"""起一个人的专属宿主。两种起法，接口一样：

- ProcessLauncher：在业务服务这台机器上直接起宿主进程。只给本机开发和测试用：插件代码没有容器隔离；
- 容器的起法在 docker_launcher.py（部署用）。

专属宿主和共享宿主是同一份 DSH 组合，另外把这个人生效的上传插件装进去：压缩包解开即用（不跑包管理器），在配置档的
node_modules 里放一个指向它的链接、把包名加进配置档的插件包清单，宿主启动时按包里自带的配置补丁加载。
每人一个持久目录当 DSH_HOME（会话日志、插件写在主目录里的数据）：宿主回收后再起，会话接得上。
试装（ephemeral）起的宿主不要持久目录：主目录用完就删，不碰任何人的会话（hosts/trial.py）。

启动器同时是「现在有哪些专属宿主在跑」的出处（find、running）：容器起法问 Docker，几个业务服务进程看到的是同一份，
谁都能接过别人建的宿主；进程起法只有本进程知道（只给本机开发，单进程）。
"""

import asyncio
import contextlib
import hashlib
import io
import json
import logging
import os
import secrets
import shutil
import tarfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)

PROFILE = "knowwork-host"
PROFILE_FILES = ("package.json", "cordis.yml", "cordis.patch.yml", "pnpm-workspace.yaml")
START_TIMEOUT_SECONDS = 90.0
STOP_TIMEOUT_SECONDS = 10.0
MAX_LOG_BYTES = 64 * 1024  # 宿主最近的输出留这么多：试装时从里面找插件加载失败的原因


@dataclass(frozen=True)
class HostKey:
    """专属宿主按「空间 + 人」分：同一个人在两个空间是两个宿主。"""

    workspace_id: str
    user_id: str


@dataclass(frozen=True)
class HostPlugin:
    """要装进宿主的一个上传插件；load 取压缩包内容（宿主上已经有这份摘要的就不取）。"""

    name: str
    digest: str
    load: Callable[[], Awaitable[bytes]]


def signature_of(plugins: Sequence[HostPlugin]) -> str:
    """装了哪些插件（按摘要）的指纹：插件变了指纹就变，宿主要换新的。"""
    digests = sorted(plugin.digest for plugin in plugins)
    return hashlib.sha256("\n".join(digests).encode()).hexdigest()[:32] if digests else ""


class Launched(Protocol):
    base_url: str
    token: str
    signature: str  # signature_of(装进去的插件)
    plugins: int  # 装了几个插件

    async def stop(self) -> None: ...

    def alive(self) -> bool: ...

    async def logs(self) -> str:
        """宿主最近的输出（试装时找加载失败的原因）。"""
        ...


class HostLauncher(Protocol):
    async def launch(self, key: HostKey, plugins: Sequence[HostPlugin], *, ephemeral: bool = False) -> Launched:
        """ephemeral：试装用，不要这个人的持久目录，停掉时连主目录一起删。
        这个人已经有一个在跑或正在建的（别的业务服务进程刚建的）抛 LaunchRaced，调用方改用 find 接过来。"""
        ...

    async def find(self, key: HostKey) -> Launched | None:
        """这个人在跑的宿主（谁建的都算）；没有返回 None。"""
        ...

    async def running(self) -> list[tuple[HostKey, Launched]]:
        """全部在跑的专属宿主（不含试装的）；顺手清掉已经退出的。"""
        ...

    async def aclose(self) -> None:
        """业务服务进程退出时：进程起法停掉自己起的宿主；容器起法什么都不做（宿主留给别的进程和重启后的自己接着用）。"""
        ...

    async def remove_home(self, key: HostKey) -> None:
        """删这个人的持久目录（会话日志、插件存的数据）。定期清理用，调用方保证这时他的宿主没在跑。"""
        ...


class LaunchFailed(RuntimeError):
    """detail：宿主最后的输出（起不来的原因多半在里面），试装时给上传的人看。"""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


class LaunchRaced(Exception):
    """这个人的宿主已经有人建了（别的业务服务进程）：不重复建，改用 find 接过来。"""


def unpack_plugin(archive: bytes, dest: Path) -> None:
    """把插件压缩包解到 dest（去掉唯一的顶层目录）。压缩包上传时已经查过（plugins/archive.py）；这里用 tarfile 的
    data 过滤器再挡一遍链接、设备文件与越出目录的路径。先解到旁边的临时目录再改名：中途断了不留半个插件。"""
    staging = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}")
    staging.mkdir(parents=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            tar.extractall(staging, filter="data")
        tops = [entry for entry in staging.iterdir()]
        if len(tops) != 1 or not tops[0].is_dir() or not (tops[0] / "package.json").is_file():
            raise LaunchFailed("插件压缩包的布局不对")
        tops[0].rename(dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def install_profile(home: Path, *, template: Path, bundles: Mapping[str, Path], plugins: Mapping[str, Path]) -> None:
    """按模板重装配置档：template 是 runtime-host/profile；bundles 是我们自己的配置层（包名 → 目录）；
    plugins 是上传的插件（包名 → 解开的目录），排在最后、加进插件包清单。"""
    profile = home / "profiles" / PROFILE
    shutil.rmtree(profile, ignore_errors=True)
    profile.mkdir(parents=True)
    for name in PROFILE_FILES:
        shutil.copy(template / name, profile / name)
    for name, target in {**plugins, **bundles}.items():  # 同名时我们自己的配置层说了算
        link = profile / "node_modules" / name
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target)
    extra = [name for name in plugins if name not in bundles]
    if extra:
        manifest = json.loads((profile / "package.json").read_text())
        manifest["dsh"]["profile"]["bundles"].extend(extra)
        (profile / "package.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


class _Output:
    """宿主输出的最后 MAX_LOG_BYTES 字节。"""

    def __init__(self, seed: Sequence[bytes] = ()) -> None:
        self._data = b"".join(seed)[-MAX_LOG_BYTES:]

    def add(self, chunk: bytes) -> None:
        self._data = (self._data + chunk)[-MAX_LOG_BYTES:]

    def text(self) -> str:
        return self._data.decode(errors="replace")


class _Process:
    def __init__(self, proc: asyncio.subprocess.Process, base_url: str, token: str, drains: list[asyncio.Task],
                 output: _Output, cleanup: Callable[[], None] | None = None, *, signature: str = "",
                 plugins: int = 0) -> None:
        self._proc = proc
        self._drains = drains
        self._output = output
        self._cleanup = cleanup
        self.base_url = base_url
        self.token = token
        self.signature = signature
        self.plugins = plugins

    def alive(self) -> bool:
        return self._proc.returncode is None

    async def logs(self) -> str:
        return self._output.text()

    async def stop(self) -> None:
        if self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=STOP_TIMEOUT_SECONDS)
            except TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        for task in self._drains:
            task.cancel()
        await asyncio.gather(*self._drains, return_exceptions=True)
        if self._cleanup is not None:
            await asyncio.to_thread(self._cleanup)


def _safe(value: str) -> str:
    """编号当目录名用：只留字母、数字、点、下划线、连字符（空间与用户编号本来就是这个字符集）。"""
    cleaned = "".join(ch for ch in value if ch.isalnum() or ch in "._-")
    if not cleaned or cleaned in (".", "..") or cleaned != value:
        raise LaunchFailed("编号里有不能当目录名的字符")
    return cleaned


class ProcessLauncher:
    """本机开发与测试：直接起宿主进程。runtime_dir 是 runtime-host 的目录（里面有 node_modules、profile、plugins）；
    root 下面 plugins/<摘要>/ 放解开的插件（按摘要共用、只读），homes/<空间>/<人>/ 是每个人的 DSH_HOME。"""

    def __init__(self, *, runtime_dir: str | Path, root: str | Path, env: Mapping[str, str],
                 node_bin: str = "", patches: Sequence[str | Path] = ()) -> None:
        self._runtime = Path(runtime_dir).resolve()
        self._root = Path(root).resolve()
        self._env = dict(env)
        self._node_bin = node_bin
        self._patches = [str(patch) for patch in patches]
        self._running: dict[HostKey, _Process] = {}  # 本进程起的（进程起法只有本进程知道）

    def home_of(self, key: HostKey) -> Path:
        return self._root / "homes" / _safe(key.workspace_id) / _safe(key.user_id)

    async def remove_home(self, key: HostKey) -> None:
        await asyncio.to_thread(shutil.rmtree, self.home_of(key), True)

    async def _plugin_dir(self, plugin: HostPlugin) -> Path:
        dest = self._root / "plugins" / plugin.digest
        if not (dest / "package.json").is_file():
            archive = await plugin.load()
            await asyncio.to_thread(self._unpack_readonly, archive, dest)
        return dest

    @staticmethod
    def _unpack_readonly(archive: bytes, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        unpack_plugin(archive, dest)
        for path in [dest, *dest.rglob("*")]:  # 几个宿主共用这一份：去掉写权限，插件改不了自己的代码
            path.chmod(path.stat().st_mode & ~0o222)

    async def find(self, key: HostKey) -> Launched | None:
        launched = self._running.get(key)
        return launched if launched is not None and launched.alive() else None

    async def running(self) -> list[tuple[HostKey, Launched]]:
        for key in [key for key, launched in self._running.items() if not launched.alive()]:
            await self._running.pop(key).stop()
        return list(self._running.items())

    async def aclose(self) -> None:
        for launched in list(self._running.values()):
            with contextlib.suppress(Exception):
                await launched.stop()
        self._running.clear()

    async def launch(self, key: HostKey, plugins: Sequence[HostPlugin], *, ephemeral: bool = False) -> Launched:
        if not ephemeral and await self.find(key) is not None:
            raise LaunchRaced()
        home = (self._root / "trials" / f"{_safe(key.workspace_id)}-{_safe(key.user_id)}") if ephemeral \
            else self.home_of(key)
        if ephemeral:
            await asyncio.to_thread(shutil.rmtree, home, True)
        dirs = {plugin.name: await self._plugin_dir(plugin) for plugin in plugins}
        bundles = {"@knowwork/dsh-host": self._runtime / "plugins" / "dsh-host",
                   "@knowwork/dsh-host-bundle": self._runtime / "plugins" / "host-bundle"}
        await asyncio.to_thread(install_profile, home, template=self._runtime / "profile", bundles=bundles, plugins=dirs)
        token = secrets.token_hex(32)
        path = f"{self._node_bin}:{os.environ.get('PATH', '')}" if self._node_bin else os.environ.get("PATH", "")
        env = {**os.environ, "PATH": path, **self._env, "DSH_HOME": str(home), "KNOWWORK_HOST_TOKEN": token,
               "KNOWWORK_HOST_PORT": "0"}
        args = ["--profile", PROFILE, *(arg for patch in self._patches for arg in ("--patch", patch))]
        proc = await asyncio.create_subprocess_exec(
            str(self._runtime / "node_modules" / ".bin" / "dsh"), *args, cwd=str(self._runtime), env=env,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        tail: list[bytes] = []
        try:
            port = await asyncio.wait_for(self._ready(proc, tail), timeout=START_TIMEOUT_SECONDS)
        except (TimeoutError, LaunchFailed) as error:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            detail = b"".join(tail)[-4000:].decode(errors="replace")
            log.warning("plugin host for %s/%s did not start: %s", key.workspace_id, key.user_id, detail[-2000:])
            if ephemeral:
                await asyncio.to_thread(shutil.rmtree, home, True)
            raise LaunchFailed("专属宿主没有起来", detail) from error
        output = _Output(tail)
        drains = [asyncio.create_task(_drain(proc.stdout, output)), asyncio.create_task(_drain(proc.stderr, output))]
        running = self._running

        def cleanup() -> None:
            if ephemeral:
                shutil.rmtree(home, ignore_errors=True)
            elif running.get(key) is launched:
                running.pop(key, None)

        launched = _Process(proc, f"http://127.0.0.1:{port}", token, drains, output, cleanup,
                            signature=signature_of(plugins), plugins=len(plugins))
        if not ephemeral:
            running[key] = launched
        return launched

    @staticmethod
    async def _ready(proc: asyncio.subprocess.Process, tail: list[bytes]) -> int:
        assert proc.stdout is not None and proc.stderr is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                tail.append(await proc.stderr.read())
                raise LaunchFailed("宿主进程退出了")
            tail.append(line)
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict) and parsed.get("knowworkHostReady"):
                return int(parsed["port"])


async def _drain(pipe: asyncio.StreamReader | None, output: _Output) -> None:
    """一直读走宿主的输出，否则管道写满会把宿主卡住；最后一段留着（试装时找原因）。"""
    if pipe is None:
        return
    while chunk := await pipe.read(65536):
        output.add(chunk)
