"""扩展给一段对话定的范围：对话顶部显示的名字、这段对话固定用哪种回答方式、能用的知识库、技能与 MCP 服务收窄到哪些。
扩展在业务服务启动时登记提供者（ApiContext.conversation_scopes），之后只读；没登记、或提供者对这段对话不管（返回
None）时，和没有这个功能时一样。

- 收窄只会更少：编译运行配置时（profile/compiler.py 的 ProfileNarrowing）和本人的权限、这一轮的选择取交集，
  扩展给不了本人没有的；
- 固定的回答方式：这段对话每一轮都按它，输入框里选的不算（前端取到范围就不让选）；这个人能不能用照常核对；
- 每一轮的说明不在这里，扩展经每轮补的上下文给（turn_context.py）；
- 扩展自己的资源（例如数据源）写在 resources 里（<类型>:<id>），开源这边不解读，由那类资源的扩展自己按它收窄；
- 提供者出错：这一轮不发、会话不打开（失败关闭），免得范围没收窄成、回答却按用户以为的范围说。
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..kernel.contract import ANSWER_MODE_PATTERN
from ..profile.snapshot import ProfileNarrowing, base_in

log = logging.getLogger(__name__)

MODE = re.compile(ANSWER_MODE_PATTERN)
MAX_LABEL = 100


@dataclass(frozen=True)
class ConversationScope:
    label: str  # 对话顶部显示的名字
    answer_mode: str | None = None  # 固定的回答方式；None 不固定
    knowledge_bases: tuple[str, ...] | None = None  # 写法同 TurnSelection.knowledge_bases；None 不收窄
    skills: tuple[str, ...] | None = None
    mcp_servers: tuple[str, ...] | None = None
    resources: tuple[str, ...] | None = None  # 扩展自己的资源，<类型>:<id>

    def __post_init__(self) -> None:
        if not 0 < len(self.label) <= MAX_LABEL:
            raise ValueError("conversation scope needs a short label")
        if self.answer_mode is not None and not MODE.fullmatch(self.answer_mode):
            raise ValueError(f"invalid answer mode: {self.answer_mode!r}")

    def narrowing(self) -> ProfileNarrowing:
        return ProfileNarrowing(knowledge_bases=self.knowledge_bases, skills=self.skills, mcp_servers=self.mcp_servers)

    # 下面三个给 @ 菜单按范围收窄候选用（mention_api.py）；编译时的收窄在 profile/compiler.py，规则相同
    def allows_base(self, base_id: str) -> bool:
        """这个知识库在不在范围里：<知识源>.* 是这个来源下的全部；没收窄知识库都算在。"""
        return self.knowledge_bases is None or base_in(self.knowledge_bases, base_id)

    def allows_skill(self, name: str) -> bool:
        return self.skills is None or name in self.skills

    def allows_server(self, name: str) -> bool:
        return self.mcp_servers is None or name in self.mcp_servers


# (workspace_id, conversation_id) → 这段对话的范围；不管这段对话返回 None
ScopeProvider = Callable[[str, str], Awaitable[ConversationScope | None]]


class ScopeUnavailable(Exception):
    """扩展说这段对话有范围，却没算出来：这一轮不发。"""


class ConversationScopes:
    def __init__(self) -> None:
        self._providers: tuple[ScopeProvider, ...] = ()

    def add(self, provider: ScopeProvider) -> None:
        self._providers = (*self._providers, provider)

    def __bool__(self) -> bool:
        return bool(self._providers)

    async def of(self, workspace_id: str, conversation_id: str) -> ConversationScope | None:
        """第一个管这段对话的提供者给的范围；都不管是 None。"""
        for provider in self._providers:
            try:
                scope = await provider(workspace_id, conversation_id)
            except Exception as error:
                log.exception("scope of conversation %s unavailable", conversation_id)
                raise ScopeUnavailable(conversation_id) from error
            if scope is not None:
                return scope
        return None

    async def narrowing(self, workspace_id: str, conversation_id: str) -> ProfileNarrowing | None:
        """编译运行配置时用（profile/facts.py）；出错照样抛，那边按「配置取不到」不打开会话。"""
        scope = await self.of(workspace_id, conversation_id)
        return None if scope is None else scope.narrowing()
