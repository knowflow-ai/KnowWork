"""会话侧的运行配置：每轮开始时重算，并记住会话当前绑定的版本。

ConversationService 只依赖这里的三个方法；会话对象只需有 id、workspace_id、user_id。
"""

import logging
from typing import Protocol

from pydantic import ValidationError

from .bindings import ProfileBindings
from .facts import ProfileCompiler
from .snapshot import NO_SELECTION, RuntimeProfile, TurnSelection

log = logging.getLogger(__name__)


class ConversationLike(Protocol):
    id: str
    workspace_id: str
    user_id: str


class ConversationProfiles:
    def __init__(self, compiler: ProfileCompiler, bindings: ProfileBindings) -> None:
        self._compiler = compiler
        self._bindings = bindings

    async def compile(self, conversation: ConversationLike, selection: TurnSelection = NO_SELECTION) -> RuntimeProfile:
        return await self._compiler.compile(workspace_id=conversation.workspace_id, user_id=conversation.user_id,
                                            session_id=conversation.id, selection=selection)

    async def bound_version(self, conversation: ConversationLike) -> str | None:
        return await self._bindings.version_of(conversation.workspace_id, conversation.id)

    async def bound(self, conversation: ConversationLike) -> RuntimeProfile | None:
        """会话当前绑定的配置；没绑定，或存的快照读不回来（结构变过）时为 None，调用方按「没绑定」处理。"""
        try:
            return await self._bindings.current(conversation.workspace_id, conversation.id)
        except ValidationError:
            log.warning("bound runtime profile of %s is unreadable; treating as unbound", conversation.id)
            return None

    async def bound_host_version(self, conversation: ConversationLike) -> str | None:
        """当前绑定的宿主相关版本（见 RuntimeProfile.host_version）。"""
        bound = await self.bound(conversation)
        return None if bound is None else bound.host_version

    async def bind(self, conversation: ConversationLike, profile: RuntimeProfile) -> None:
        await self._bindings.bind(conversation.workspace_id, conversation.id, profile)
