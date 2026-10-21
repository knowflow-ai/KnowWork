"""会话侧的运行配置：每轮开始时重算，并记住会话当前绑定的版本（技术方案 3.3、5.3 节）。

ConversationService 只依赖这里的三个方法；会话对象只需有 id、workspace_id、user_id。
"""

from typing import Protocol

from .bindings import ProfileBindings
from .facts import ProfileCompiler
from .snapshot import NO_SELECTION, RuntimeProfile, TurnSelection


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

    async def bind(self, conversation: ConversationLike, profile: RuntimeProfile) -> None:
        await self._bindings.bind(conversation.workspace_id, conversation.id, profile)
