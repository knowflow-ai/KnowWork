"""按对话把内核合同的调用送到对的宿主：共享宿主，或这个人的专属宿主。上层模块照旧只认内核合同，不知道有几个宿主。

去处在新开对话时定、之后不变（记在 placement 里）：这个人此刻有生效的、适用于云端对话的上传插件，这段对话就去他的
专属宿主，否则在共享宿主。不中途搬家：一段对话的官方会话日志只在一个宿主上，搬就要搬日志；所以插件的开关对新开的对话
生效，页面上也是这么说的。已经在专属宿主上的对话，插件全关掉以后仍然在那边（宿主里没有插件而已）。

专属宿主被回收后，这段对话的调用报「宿主上没有这个会话」：对话服务本来就按这个错误走恢复（重新打开、恢复会话），
恢复打开时这里把宿主再建起来。
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Protocol, TypeVar

import httpx

from ..kernel.contract import (
    AgentKernel,
    BundledSkill,
    CancelResult,
    CommandOutcome,
    HostCommand,
    HostModel,
    KernelEvent,
    PendingApproval,
    PendingQuestion,
    QuestionAnswer,
    SessionNotFound,
    SessionSpec,
    TurnInput,
)
from ..kernel.host_client import HostKernel
from .dedicated import DedicatedHosts
from .launcher import HostKey, HostPlugin

log = logging.getLogger(__name__)
T = TypeVar("T")

# (workspace_id, user_id) → 这个人此刻要装进专属宿主的上传插件
PluginsOf = Callable[[str, str], Awaitable[Sequence[HostPlugin]]]
# 给某个人的专属宿主签模型令牌（带归属，见 kernel/tokens.py）
OwnerToken = Callable[[HostKey], str]


class Placement(Protocol):
    """一段对话落在哪：None 是共享宿主，否则是专属宿主的属主。"""

    async def get(self, session_id: str) -> HostKey | None: ...

    async def decided(self, session_id: str) -> bool: ...

    async def put(self, session_id: str, workspace_id: str, key: HostKey | None) -> None: ...


class MemoryPlacement:
    """测试用：只记在内存里。"""

    def __init__(self) -> None:
        self._where: dict[str, HostKey | None] = {}

    async def get(self, session_id: str) -> HostKey | None:
        return self._where.get(session_id)

    async def decided(self, session_id: str) -> bool:
        return session_id in self._where

    async def put(self, session_id: str, workspace_id: str, key: HostKey | None) -> None:
        self._where[session_id] = key


def _gone(session_id: str) -> SessionNotFound:
    return SessionNotFound("session_not_found", f"专属宿主上没有会话 {session_id}", 404)


class RoutedKernel:
    def __init__(self, shared: HostKernel, hosts: DedicatedHosts, placement: Placement, plugins_of: PluginsOf, *,
                 owner_token: OwnerToken | None = None, activation_token: OwnerToken | None = None) -> None:
        self._shared = shared
        self._owner_token = owner_token
        self._activation_token = activation_token
        self._hosts = hosts
        self._placement = placement
        self._plugins_of = plugins_of
        # 对话的去处在内存里再记一份：每次调用都查库太慢。None 是共享宿主
        self._known: dict[str, HostKey | None] = {}
        # 最近一次同步给宿主的模型清单：新建的专属宿主拿同一份
        self.last_models: tuple[HostModel, ...] | None = None

    async def _key(self, session_id: str) -> HostKey | None:
        if session_id not in self._known:
            self._known[session_id] = await self._placement.get(session_id)
        return self._known[session_id]

    async def _of(self, session_id: str) -> tuple[HostKey | None, AgentKernel]:
        key = await self._key(session_id)
        if key is None:
            return None, self._shared
        kernel = await self._hosts.kernel_of(key)
        if kernel is None:  # 宿主回收了或退了：让调用方走恢复
            raise _gone(session_id)
        return key, kernel

    async def _call(self, session_id: str, call: Callable[[AgentKernel], Awaitable[T]]) -> T:
        """按对话转发。专属宿主连不上（别的业务服务进程刚把它收了、它自己退了）：放掉本进程连着的客户端，
        报「宿主上没有这个会话」，调用方照常走恢复，重新打开时把宿主再建起来或接过别的进程新建的。"""
        key, kernel = await self._of(session_id)
        try:
            return await call(kernel)
        except httpx.ConnectError as error:
            if key is None:
                raise
            await self._hosts.lost(key)
            raise _gone(session_id) from error

    # ---- 开会话：定去处 ----

    async def open_session(self, spec: SessionSpec) -> str:
        key = await self._place(spec)
        if key is None:
            return await self._shared.open_session(spec)
        plugins = await self._plugins_of(key.workspace_id, key.user_id)
        kernel = await self._hosts.ensure(key, plugins)
        try:
            return await kernel.open_session(spec)
        except httpx.ConnectError:  # 连着的是已经被收掉的那台：放掉，再来一次（这次会新建或接过别人建的）
            await self._hosts.lost(key)
            return await (await self._hosts.ensure(key, plugins)).open_session(spec)

    async def _place(self, spec: SessionSpec) -> HostKey | None:
        session_id = spec.session_id
        if session_id in self._known or await self._placement.decided(session_id):
            return await self._key(session_id)
        key: HostKey | None = None
        if spec.owner is not None and not spec.resume:
            owner = HostKey(spec.owner.workspace_id, spec.owner.user_id)
            if await self._plugins_of(owner.workspace_id, owner.user_id):
                key = owner
        if spec.owner is None:  # 没有归属的会话（内部任务）不记，一直在共享宿主
            self._known[session_id] = None
            return None
        await self._placement.put(session_id, spec.owner.workspace_id, key)
        return await self._key(session_id)  # 以存下来的为准：别的进程同时定过的话，用先到的那个

    # ---- 按对话转发 ----

    async def send(self, session_id: str, turn: TurnInput) -> str:
        return await self._call(session_id, lambda kernel: kernel.send(session_id, turn))

    async def cancel(self, session_id: str) -> CancelResult:
        return await self._call(session_id, lambda kernel: kernel.cancel(session_id))

    async def retitle(self, session_id: str, title: str) -> None:
        await self._call(session_id, lambda kernel: kernel.retitle(session_id, title))

    async def dispose(self, session_id: str) -> None:
        key = await self._key(session_id)
        self._known.pop(session_id, None)  # 去处还在 placement 里，下次用到再读
        if key is None:
            await self._shared.dispose(session_id)
            return
        kernel = await self._hosts.kernel_of(key)
        if kernel is None:  # 宿主已经不在：会话本来就不在内存里了
            return
        try:
            await kernel.dispose(session_id)
        except httpx.ConnectError:
            await self._hosts.lost(key)

    async def pending_approvals(self, session_id: str) -> list[PendingApproval]:
        return await self._call(session_id, lambda kernel: kernel.pending_approvals(session_id))

    async def decide_approval(self, session_id: str, approval_id: str, allow: bool) -> None:
        await self._call(session_id, lambda kernel: kernel.decide_approval(session_id, approval_id, allow))

    async def pending_questions(self, session_id: str) -> list[PendingQuestion]:
        return await self._call(session_id, lambda kernel: kernel.pending_questions(session_id))

    async def answer_question(self, session_id: str, question_id: str, answer: QuestionAnswer) -> None:
        await self._call(session_id, lambda kernel: kernel.answer_question(session_id, question_id, answer))

    async def rotate_tool_credential(self, session_id: str, token: str) -> None:
        await self._call(session_id, lambda kernel: kernel.rotate_tool_credential(session_id, token))

    async def commands(self, session_id: str) -> tuple[HostCommand, ...]:
        return await self._call(session_id, lambda kernel: kernel.commands(session_id))

    async def run_command(self, session_id: str, line: str) -> CommandOutcome:
        return await self._call(session_id, lambda kernel: kernel.run_command(session_id, line))

    async def goal_armed(self, session_id: str) -> bool:
        return await self._call(session_id, lambda kernel: kernel.goal_armed(session_id))

    async def rotate_skill_credential(self, session_id: str, token: str) -> None:
        await self._call(session_id, lambda kernel: kernel.rotate_skill_credential(session_id, token))

    async def subscribe(self, session_id: str, *, positions: Mapping[str, int] | None = None) -> AsyncIterator[KernelEvent]:
        """有订阅者的会话宿主自己算「在用」（GET /v1/activity 的 busy），回答很长的一轮不会被当成空闲收掉。"""
        key, kernel = await self._of(session_id)
        try:
            async for event in kernel.subscribe(session_id, positions=positions):
                yield event
        except httpx.ConnectError as error:
            if key is None:
                raise
            await self._hosts.lost(key)
            raise _gone(session_id) from error

    # ---- 宿主范围的：共享宿主加全部专属宿主 ----

    async def rotate_model_credential(self, token: str) -> None:
        """共享宿主拿传进来的平台令牌；专属宿主各拿一份按这个人签的（给了 owner_token 时），连同按人签的激活令牌
        （有效期同模型令牌，一起续）。"""
        await self._shared.rotate_model_credential(token)
        owner_token, activation_token = self._owner_token, self._activation_token

        async def rotate(key: HostKey, kernel: HostKernel) -> None:
            await kernel.rotate_model_credential(owner_token(key) if owner_token is not None else token)
            if activation_token is not None:
                await kernel.rotate_activation_credential(activation_token(key))

        await self._each("rotate model credential", rotate)

    async def sync_models(self, models: tuple[HostModel, ...]) -> None:
        await self._shared.sync_models(models)
        self.last_models = models
        await self._each("sync models", lambda _, kernel: kernel.sync_models(models))

    async def _each(self, what: str, call: Callable[[HostKey, HostKernel], Awaitable[None]]) -> None:
        """专属宿主一个出错不连累别的：它下次建起来时会拿到最新的。"""
        for key, kernel in await self._hosts.kernels():
            try:
                await call(key, kernel)
            except Exception:
                log.warning("plugin host failed to %s", what, exc_info=True)

    async def bundled_skills(self) -> tuple[BundledSkill, ...]:
        return await self._shared.bundled_skills()

    async def aclose(self) -> None:
        await self._hosts.aclose()
        await self._shared.aclose()
