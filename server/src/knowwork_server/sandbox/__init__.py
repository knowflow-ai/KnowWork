"""会话沙箱：一会话一个长驻 Docker 容器，工作区在 /workspace。

模型用的是宿主里 DSH 官方的 bash、文件工具与 present：它们经宿主的远程执行器回调本包的内部接口（internal_api）
落到会话容器。装配：`build_sandbox(SandboxSettings())` 返回 SandboxModule；没有打开 KNOWWORK_SANDBOX_DOCKER_ENABLED
时返回 None，这时 EXECUTION_TOOLS 进每个会话的 deny，模型看不到这些工具。
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from .config import ContainerPosture, SandboxSettings
from .containers import ContainerManager, CreatedHook
from .contract import SessionSandbox
from .docker_engine import DockerEngine
from .docker_host import ClientFactory, client_factory
from .docker_sandbox import DockerSessionSandbox
from .ssh_pipe import SshPipes
from .sweeper import IdleSweeper, LiveCheck, reap_orphans

log = logging.getLogger(__name__)

# 沙箱不建表：容器就是它的状态，按 label 找回
SANDBOX_RLS_TABLES: tuple[str, ...] = ()

# 宿主里落到沙箱执行的官方工具（dsh-tool-bash、dsh-tool-fs、dsh-tool-present、dsh-tool-jobs）；没开沙箱时一律收掉
EXECUTION_TOOLS: tuple[str, ...] = ("bash", "read", "read_image", "write", "edit", "present", "job_output", "job_kill", "job_list")


class ConversationRef(Protocol):
    id: str
    workspace_id: str


def sandbox_eraser(sandbox: SessionSandbox) -> Callable[[ConversationRef], Awaitable[None]]:
    """会话删除时删掉容器（连匿名卷）。删不掉就让删除失败（会话留着可重试），不留下无主的工作区。"""

    async def erase(conversation: ConversationRef) -> None:
        await sandbox.destroy(conversation.workspace_id, conversation.id)

    return erase


@dataclass
class SandboxModule:
    sandbox: DockerSessionSandbox
    containers: ContainerManager
    sweeper: IdleSweeper
    engine: DockerEngine
    orphan_grace_seconds: float
    max_timeout_seconds: float
    ssh: SshPipes | None = None  # 流式执行（KNOWWORK_SANDBOX_SSH_ENABLED）没打开时为空

    def eraser(self) -> Callable[[ConversationRef], Awaitable[None]]:
        return sandbox_eraser(self.sandbox)

    def on_created(self, hook: CreatedHook) -> None:
        """新建了容器时回调（工作空间, 会话），主线接附件 restage：容器被回收后工作区是空的。"""
        self.containers.add_created_hook(hook)

    async def reap_orphans(self, is_live: LiveCheck) -> int:
        """删掉会话已不存在的沙箱容器；is_live 由主线提供。默认不定时调用。"""
        return await reap_orphans(self.engine, self.containers, is_live, grace_seconds=self.orphan_grace_seconds)

    def start(self) -> None:
        """服务启动时清扫一次（之后只在创建、连接沙箱时触发）；在事件循环里调用。"""
        self.sweeper.trigger()

    async def aclose(self) -> None:
        await self.sweeper.aclose()
        if self.ssh is not None:
            await self.ssh.aclose()
        await self.engine.aclose()


def build_sandbox(settings: SandboxSettings, *, factory: ClientFactory | None = None) -> SandboxModule | None:
    if not settings.docker_enabled:
        return None
    factory = factory or client_factory(settings.docker_host, settings.docker_tls_cert_path,
                                        timeout_seconds=settings.http_timeout_seconds)
    engine = DockerEngine(factory, pull_timeout_seconds=settings.pull_timeout_seconds,
                          max_streams=settings.max_concurrent_execs)
    containers = ContainerManager(engine, ContainerPosture.of(settings), idle_ttl_seconds=settings.idle_ttl_seconds)
    sweeper = IdleSweeper(engine, containers, default_ttl_seconds=settings.idle_ttl_seconds,
                          min_interval_seconds=settings.sweep_min_interval_seconds)
    containers.on_sweep = sweeper.trigger  # 创建、连接沙箱时在后台触发清扫
    sandbox = DockerSessionSandbox(engine, containers, user=settings.exec_user,
                                   max_input_bytes=settings.max_input_bytes, max_write_bytes=settings.max_write_bytes,
                                   max_timeout_seconds=settings.exec_max_timeout_seconds)
    ssh = SshPipes(engine, containers, exec_user=settings.exec_user, max_pipes=settings.ssh_max_pipes) \
        if settings.ssh_enabled else None
    return SandboxModule(sandbox=sandbox, containers=containers, sweeper=sweeper, engine=engine,
                         orphan_grace_seconds=settings.orphan_grace_seconds,
                         max_timeout_seconds=settings.exec_max_timeout_seconds, ssh=ssh)


__all__ = ["EXECUTION_TOOLS", "SANDBOX_RLS_TABLES", "SandboxModule", "SandboxSettings", "build_sandbox", "sandbox_eraser"]
