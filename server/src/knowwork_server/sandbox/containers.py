"""一会话一个长驻容器（照抄 WeKnora 的 docker_remote_client.go）：按需创建、按 label 找回、停了就拉起、
配置变了重建、销毁时连匿名卷一起删。

- 工作区不挂卷，就在容器自己的文件系统里（WeKnora 同此：它的 Docker 后端不支持卷，容器被删后「之前的文件
  不会保留」）。容器被空闲清扫或配置变更删掉后，下一次使用会新建一个空的；新建时调用 on_created 钩子，
  主线把它接到附件的 restage 上，把已就绪的附件重新放进去。产物在每轮结束时已收走，不受影响。
- 容器名由（工作空间, 会话）的摘要确定，同一会话不管哪个进程来找都落到同一个名字上；找到同名容器时再核对
  label，不是我们为这个会话建的就拒绝，不收养。
"""

import asyncio
import contextlib
import hashlib
import logging
import re
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .config import ContainerPosture
from .contract import SandboxError
from .docker_engine import DockerConflict, DockerEngine
from .paths import WORKSPACE_DIR, session_root
from .scripts import ENTRYPOINT

log = logging.getLogger(__name__)

LABEL_COMPONENT = "io.knowwork.component"  # 归属标记：清扫只碰带它的容器，与其他业务共用 daemon 也不误删
LABEL_WORKSPACE = "io.knowwork.workspace"
LABEL_SESSION = "io.knowwork.session"
LABEL_POSTURE = "io.knowwork.posture"
LABEL_IDLE_TTL = "io.knowwork.idle-ttl-seconds"
COMPONENT = "sandbox"
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
CONFLICT = "沙箱环境冲突，请联系管理员"
# CapDrop ALL 之后加回的能力，与 WeKnora 的 dockerSandboxCapabilities 相同：以 root 运行的装包工具要写镜像里的
# 目录、改属主（CHOWN/DAC_OVERRIDE/FOWNER/FSETID），apt、pip 解包时会降权（SETUID/SETGID），KILL 用来停自己的子进程。
# NET_RAW、NET_BIND_SERVICE、MKNOD、SYS_CHROOT、AUDIT_WRITE、SETPCAP、SETFCAP 不加回。
CAPABILITIES = ("CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "SETGID", "SETUID", "KILL")
START_READY_SECONDS = 15.0
START_READY_POLL = 0.05

CreatedHook = Callable[[str, str], Awaitable[object]]


@dataclass(frozen=True)
class SandboxKey:
    workspace_id: str
    session_id: str

    @classmethod
    def of(cls, workspace_id: str, session_id: str) -> "SandboxKey":
        if not (isinstance(workspace_id, str) and ID_PATTERN.match(workspace_id)
                and isinstance(session_id, str) and ID_PATTERN.match(session_id)):
            raise SandboxError("会话标识不合法")
        return cls(workspace_id, session_id)

    @classmethod
    def from_labels(cls, labels: dict[str, str] | None) -> "SandboxKey | None":
        labels = labels or {}
        try:
            return cls.of(labels.get(LABEL_WORKSPACE, ""), labels.get(LABEL_SESSION, ""))
        except SandboxError:
            return None

    @property
    def name(self) -> str:
        digest = hashlib.sha256(f"{self.workspace_id}\x00{self.session_id}".encode()).hexdigest()[:32]
        return f"kw-sbx-{digest}"

    def labels(self) -> dict[str, str]:
        return {LABEL_COMPONENT: COMPONENT, LABEL_WORKSPACE: self.workspace_id, LABEL_SESSION: self.session_id}

    def owns(self, labels: dict[str, str] | None) -> bool:
        labels = labels or {}
        return all(labels.get(key) == value for key, value in self.labels().items())


def container_options(key: SandboxKey, posture: ContainerPosture, *, idle_ttl_seconds: int) -> dict[str, Any]:
    """docker-py containers.create 的参数（姿态照抄 WeKnora 的 Create 与 hostConfig）。"""
    options: dict[str, Any] = {
        "image": posture.image,
        # 用 entrypoint 而不是 command，并把 command 清空：否则镜像自带的 ENTRYPOINT 会接管 PID 1
        "entrypoint": list(ENTRYPOINT), "command": [],
        # PID 1 固定 uid 0：入口要在 /var/lib 下建活动标记；镜像最后一行是非 root USER 时也照样成立
        "user": "0", "working_dir": WORKSPACE_DIR,
        "environment": ["LANG=C.UTF-8", "PYTHONUNBUFFERED=1", f"KW_SESSION_ROOT={session_root(key.session_id)}"],
        "labels": {**key.labels(), LABEL_POSTURE: posture.digest, LABEL_IDLE_TTL: str(idle_ttl_seconds)},
        "cap_drop": ["ALL"], "cap_add": list(CAPABILITIES), "security_opt": ["no-new-privileges"],
        # 入口是不调 wait() 的 sleep；没有 init（tini）在前面，后台进程退出后成僵尸，久了占满进程数上限
        "init": True,
        # 内存与内存+交换相等即关掉交换：失控的分配直接被杀，不去拖垮宿主机磁盘
        "mem_limit": posture.memory_bytes, "memswap_limit": posture.memory_bytes,
        "nano_cpus": posture.nano_cpus, "pids_limit": posture.pids_limit,
        "network_mode": posture.network_mode, "restart_policy": {"Name": "no"},
    }
    if posture.runtime:
        options["runtime"] = posture.runtime
    return options


def _status(info: dict[str, Any]) -> str:
    return str((info.get("State") or {}).get("Status") or "").lower()


class ContainerManager:
    def __init__(self, engine: DockerEngine, posture: ContainerPosture, *, idle_ttl_seconds: int) -> None:
        self._engine = engine
        self._posture = posture
        self._idle_ttl = idle_ttl_seconds
        self.on_sweep: Callable[[], None] | None = None  # 装配时接空闲清扫的 trigger
        self._locks: weakref.WeakValueDictionary[SandboxKey, asyncio.Lock] = weakref.WeakValueDictionary()
        self._busy: dict[SandboxKey, int] = {}
        self._created_hooks: list[CreatedHook] = []

    def lock(self, key: SandboxKey) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def is_busy(self, key: SandboxKey) -> bool:
        return bool(self._busy.get(key))

    def add_created_hook(self, hook: CreatedHook) -> None:
        """新建了容器（第一次、或被回收后重建）时调用，参数是（工作空间, 会话）。"""
        self._created_hooks.append(hook)

    @contextlib.asynccontextmanager
    async def using(self, key: SandboxKey, *, create: bool = True) -> AsyncIterator[str | None]:
        """拿到运行中的容器 ID（create=False 且不存在时为 None）；使用期间不会被空闲清扫删掉。"""
        self._busy[key] = self._busy.get(key, 0) + 1
        try:
            async with self.lock(key):
                container_id, created = await self._ensure(key, create=create)
            if self.on_sweep is not None:
                self.on_sweep()
            if created:
                await self._run_created_hooks(key)
            yield container_id
        finally:
            remaining = self._busy.get(key, 1) - 1
            if remaining > 0:
                self._busy[key] = remaining
            else:
                self._busy.pop(key, None)

    async def _run_created_hooks(self, key: SandboxKey) -> None:
        for hook in self._created_hooks:
            try:
                await hook(key.workspace_id, key.session_id)
            except Exception:  # 钩子失败（例如附件重放失败）不影响这次操作，失败由钩子自己记录与告知
                log.warning("沙箱新建后的钩子执行失败", exc_info=True)

    async def _ensure(self, key: SandboxKey, *, create: bool) -> tuple[str | None, bool]:
        info = await self._engine.inspect_container(key.name)
        if info is not None:
            labels = (info.get("Config") or {}).get("Labels")
            if not key.owns(labels):
                log.warning("沙箱容器名被占用且 label 不符，拒绝收养")
                raise SandboxError(CONFLICT)
            if (labels or {}).get(LABEL_POSTURE) == self._posture.digest and _status(info) != "dead":
                return await self._connect(info), False
            log.info("沙箱配置已变更或容器已失效，删除重建（工作区不保留）")
            await self._engine.remove_container(str(info["Id"]))
        if not create:
            return None, False
        return await self._create(key), True

    async def _connect(self, info: dict[str, Any]) -> str:
        """停了的容器文件系统还在，拉起来接着用（WeKnora 的 Connect：Docker 里最接近「暂停后自动恢复」的做法）。"""
        container_id = str(info["Id"])
        status = _status(info)
        if status == "running":
            return container_id
        if status == "paused":
            await self._engine.unpause_container(container_id)
        elif status in ("exited", "created"):
            await self._engine.start_container(container_id)
        await self._wait_running(container_id)
        return container_id

    async def _wait_running(self, container_id: str) -> None:
        """daemon 接受 start 不等于 PID 1 已经跑起来；这时 exec 会得到 409「容器未运行」。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + START_READY_SECONDS
        while True:
            info = await self._engine.inspect_container(container_id)
            status = _status(info) if info is not None else "missing"
            if status == "running":
                return
            if status in ("exited", "paused", "dead", "missing") or loop.time() >= deadline:
                log.warning("沙箱容器未能进入运行状态：%s", status)
                raise SandboxError("沙箱启动失败，请稍后重试")
            await asyncio.sleep(START_READY_POLL)

    async def _create(self, key: SandboxKey) -> str:
        await self._engine.ensure_image(self._posture.image)
        options = container_options(key, self._posture, idle_ttl_seconds=self._idle_ttl)
        try:
            container_id = await self._engine.create_container(key.name, options)
        except DockerConflict as error:
            raise SandboxError("沙箱正在准备中，请稍后重试") from error
        try:
            await self._engine.start_container(container_id)
            await self._wait_running(container_id)
        except BaseException:
            # 起不来的容器没有用，留着只会等到很久以后的清扫才回收
            with contextlib.suppress(Exception):
                await self._engine.remove_container(container_id)
            raise
        return container_id

    async def destroy(self, key: SandboxKey) -> None:
        async with self.lock(key):
            ids = {str(item["Id"]) for item in await self._engine.list_containers(key.labels())}
            info = await self._engine.inspect_container(key.name)
            if info is not None and key.owns((info.get("Config") or {}).get("Labels")):
                ids.add(str(info["Id"]))
            for container_id in ids:
                await self._engine.remove_container(container_id)
