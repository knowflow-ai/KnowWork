"""给主线的装配函数：按配置决定开不开个人专属宿主。不开时返回 None，业务服务只连共享宿主。"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import async_sessionmaker

from ..kernel.host_client import HostKernel
from .cleanup import HomeCleanup, Member
from .dedicated import DedicatedHosts
from .launcher import HostKey, HostLauncher, HostPlugin, ProcessLauncher
from .placement import StoredPlacement
from .routed import RoutedKernel

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024


class Settings(Protocol):
    plugin_hosts: str
    plugin_host_max: int
    plugin_host_idle_seconds: int
    plugin_host_retention_days: int
    plugin_host_image: str
    plugin_host_network: str
    plugin_host_memory_mb: int
    plugin_host_publish: bool
    plugin_host_runtime_dir: str
    plugin_host_dir: str
    plugin_host_node_bin: str
    plugin_host_callback_url: str
    plugin_host_model_base_url: str
    internal_url: str
    host_model_alias: str


class Uploads(Protocol):
    """plugins.UploadService 里用到的两样：这个人生效的云端插件、按摘要取压缩包。"""

    async def effective(self, workspace_id: str, user_id: str, target: str) -> Sequence[Any]: ...

    async def blob(self, digest: str) -> bytes: ...


def host_env(settings: Settings) -> dict[str, str]:
    """专属宿主的环境变量：和共享宿主同一套回调入口（编排里 runtime-host 的那几项）。"""
    callback = (settings.plugin_host_callback_url or settings.internal_url).rstrip("/")
    model = settings.plugin_host_model_base_url or f"{callback}/internal/model/v1"
    return {
        "KNOWWORK_MODEL": settings.host_model_alias,
        "KNOWWORK_MODEL_BASE_URL": model,
        "KNOWWORK_TOOLS_BASE_URL": f"{callback}/internal/extensions/tools",
        "KNOWWORK_SKILLS_BASE_URL": f"{callback}/internal/extensions/skills",
        "KNOWWORK_SANDBOX_BASE_URL": f"{callback}/internal/extensions/sandbox",
        "KNOWWORK_CONTENT_SAFETY_BASE_URL": f"{callback}/internal/extensions/content-safety",
        "KNOWWORK_ACTIVATION_BASE_URL": f"{callback}/internal/host/v1",
    }


def build_launcher(settings: Settings, *, docker_engine: Any | None) -> HostLauncher:
    if settings.plugin_hosts == "process":
        if not settings.plugin_host_runtime_dir:
            raise ValueError("KNOWWORK_PLUGIN_HOSTS=process 要给 KNOWWORK_PLUGIN_HOST_RUNTIME_DIR（runtime-host 的目录）")
        return ProcessLauncher(runtime_dir=settings.plugin_host_runtime_dir, root=settings.plugin_host_dir,
                               env=host_env(settings), node_bin=settings.plugin_host_node_bin)
    from .docker_launcher import DockerLauncher, HostPosture

    if docker_engine is None:
        raise ValueError("KNOWWORK_PLUGIN_HOSTS=docker 要先打开会话沙箱的 Docker 后端（专属宿主用同一个 Docker）")
    if not settings.plugin_host_image or not settings.plugin_host_network:
        raise ValueError("KNOWWORK_PLUGIN_HOSTS=docker 要给 KNOWWORK_PLUGIN_HOST_IMAGE 和 KNOWWORK_PLUGIN_HOST_NETWORK")
    posture = HostPosture(image=settings.plugin_host_image, network=settings.plugin_host_network,
                          memory_bytes=settings.plugin_host_memory_mb * 1024 * 1024,
                          publish=settings.plugin_host_publish)
    return DockerLauncher(docker_engine, posture, env=host_env(settings))


@dataclass
class PluginHosts:
    kernel: RoutedKernel
    hosts: DedicatedHosts
    sessionmaker: async_sessionmaker
    retention_days: int = 90
    _uploads: Uploads | None = None
    _cleanup: HomeCleanup | None = None

    def bind(self, uploads: Uploads, *, scoped: async_sessionmaker | None = None, member: Member | None = None) -> None:
        """插件服务建好以后接上（它要等访问控制、对象存储都建好）。接上之前谁都没有上传插件。
        给了 scoped（按空间的会话工厂）和 member（还是不是空间成员）就定期清理持久目录（cleanup.py）。"""
        self._uploads = uploads
        if scoped is not None and member is not None:
            self._cleanup = HomeCleanup(self.hosts, self.sessionmaker, scoped, member=member,
                                        retention_days=self.retention_days)

    def start(self) -> None:
        """定时回收空闲的专属宿主，定时清理持久目录。"""
        self.hosts.start(cleanup=self._cleanup.run_once if self._cleanup is not None else None)

    async def plugins_of(self, workspace_id: str, user_id: str) -> Sequence[HostPlugin]:
        uploads = self._uploads
        if uploads is None:
            return ()
        return tuple(HostPlugin(name=item.name, digest=item.digest, load=_loader(uploads, item.digest))
                     for item in await uploads.effective(workspace_id, user_id, "cloud"))


def _loader(uploads: Uploads, digest: str) -> Callable[[], Awaitable[bytes]]:
    async def load() -> bytes:
        return await uploads.blob(digest)

    return load


def build_plugin_hosts(settings: Settings, shared: HostKernel, sessionmaker: async_sessionmaker, *,
                       issue_owner_token: Callable[[HostKey], str],
                       issue_activation_token: Callable[[HostKey], str] | None = None,
                       docker_engine: Any | None = None) -> PluginHosts | None:
    """sessionmaker 用不按空间的那个（session_host 表不做行级安全）。issue_owner_token 给某个人的宿主签模型令牌：
    带着归属，模型网关见到它就按这个空间的内容安全策略再查一遍（专属宿主里的插件没审过，宿主侧的检查可能被绕开）。
    issue_activation_token 给某个人的宿主签激活令牌：长任务目标、定时任务自己发起一轮前请业务服务激活对话用，只能激活他本人的
    对话（共享宿主的令牌不能给专属宿主，conversations/activation.py）。"""
    if settings.plugin_hosts == "off":
        return None
    launcher = build_launcher(settings, docker_engine=docker_engine)
    holder: dict[str, PluginHosts] = {}

    async def prepare(key: HostKey, kernel: HostKernel) -> None:
        """新宿主建好（或接过别的进程建的）以后：给它按这个人现签的模型令牌、激活令牌，和共享宿主现在用的那份模型清单。"""
        await kernel.rotate_model_credential(issue_owner_token(key))
        if issue_activation_token is not None:
            await kernel.rotate_activation_credential(issue_activation_token(key))
        models = holder["built"].kernel.last_models
        if models is not None:
            await kernel.sync_models(models)

    async def plugins_of(workspace_id: str, user_id: str) -> Sequence[HostPlugin]:
        return await holder["built"].plugins_of(workspace_id, user_id)

    hosts = DedicatedHosts(launcher, prepare=prepare, max_hosts=settings.plugin_host_max,
                           idle_seconds=settings.plugin_host_idle_seconds)
    built = PluginHosts(kernel=RoutedKernel(shared, hosts, StoredPlacement(sessionmaker), plugins_of,
                                            owner_token=issue_owner_token, activation_token=issue_activation_token),
                        hosts=hosts, sessionmaker=sessionmaker,
                        retention_days=settings.plugin_host_retention_days)
    holder["built"] = built
    return built
