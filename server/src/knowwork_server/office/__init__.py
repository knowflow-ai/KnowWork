"""一人一个 Office 容器（镜像 knowwork-office，开源）：按人起、闲置回收，文件按对话分目录放进去、取出来，
经容器里的 MCP 门卫调 GenOffice 自带的工具。在线编辑（扩展）用它；格式转换不在这里，仍是会话沙箱里的 LibreOffice。
本机开发不起容器时，连一个固定的、从源码起的 GenOffice（files.py 的 LocalOfficeFiles）。
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from ..sandbox.docker_engine import DockerEngine
from ..sandbox.docker_host import client_factory
from .config import OfficeSettings
from .containers import OfficeContainers, OfficeKey, OfficeSpec
from .files import LocalOfficeFiles, OfficeFiles


class ConversationRef(Protocol):
    id: str
    workspace_id: str
    user_id: str


@dataclass
class OfficeRuntime:
    """业务服务里唯一的一份，交给扩展的在线编辑（ApiContext.office）：建池、清扫、关停都在业务服务。"""

    files: OfficeFiles
    containers: OfficeContainers | None = None  # 按人起容器时才有（本机开发连固定的 GenOffice）
    _closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    def start(self) -> None:
        if self.containers is not None:
            self.containers.sweeper.trigger()  # 启动时清扫一次（同会话沙箱），之后定时清扫
            self.containers.start()

    def eraser(self) -> Callable[[ConversationRef], Awaitable[None]]:
        """删对话时清掉对话主人编辑器里这段对话的文件（conversations 的 erasers）。删不掉就让删除失败，对话留着可以再删。"""

        async def erase(conversation: ConversationRef) -> None:
            await self.files.erase(OfficeKey.of(conversation.workspace_id, conversation.user_id), conversation.id)

        return erase

    async def aclose(self) -> None:
        if self.containers is not None:
            await self.containers.aclose()
        for close in reversed(self._closers):
            await close()


def author_names(get_many: Callable[[Sequence[str]], Awaitable[Mapping[str, Any]]]) -> Callable[[str], Awaitable[str]]:
    """用户 id → 修订作者名（用户资料里的名字，查不到是空、GenOffice 用它的默认名）。建容器时写进去，
    谁先建这个人的容器（api 进程打开编辑器、worker 转附件）都写一样的名字。"""

    async def author_of(user_id: str) -> str:
        found = (await get_many([user_id])).get(user_id)
        return str(getattr(found, "name", "") or "") if found is not None else ""

    return author_of


def build_office(settings: OfficeSettings, *, engine: DockerEngine | None = None,
                 author_of: Callable[[str], Awaitable[str]] | None = None) -> OfficeRuntime | None:
    """按配置建 Office 容器池；没配置（没设镜像、也没有本机开发的固定 GenOffice）时是 None。
    author_of：用户 id → 修订作者名，建容器时写进去（author_names）。"""
    if settings.local:
        assert settings.files_root is not None
        return OfficeRuntime(files=LocalOfficeFiles(settings.files_root, settings.upstream_mcp_url))
    if not settings.per_user:
        return None
    closers: list[Callable[[], Awaitable[None]]] = []
    if engine is None:
        engine = DockerEngine(client_factory(settings.docker_host, settings.docker_tls_cert_path, timeout_seconds=30.0))
        closers.append(engine.aclose)
    # 文档、图片可能很大、导出要几秒：不设读超时；连接超时短一点，容器没起来时尽快知道
    http = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10), follow_redirects=False, trust_env=False)
    closers.append(http.aclose)
    spec = OfficeSpec(image=settings.image or "", memory_bytes=settings.memory_mb * 1024 * 1024,
                      shm_bytes=settings.shm_mb * 1024 * 1024, nano_cpus=int(settings.cpus * 1e9),
                      network=settings.network, idle_ttl_seconds=settings.idle_minutes * 60)
    keep_stopped = settings.keep_stopped_days * 24 * 3600 if settings.keep_stopped_days > 0 else None
    containers = OfficeContainers(engine, spec, max_containers=settings.max_containers, http=http, author_of=author_of,
                                  prewarm=settings.prewarm, keep_stopped_seconds=keep_stopped)
    return OfficeRuntime(files=containers, containers=containers, _closers=closers)
