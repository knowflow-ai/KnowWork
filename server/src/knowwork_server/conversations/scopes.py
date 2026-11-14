"""扩展给一段对话定的范围：对话顶部显示的名字、这段对话固定用哪种回答方式、能用的知识库、技能与 MCP 服务收窄到哪些。
扩展在业务服务启动时登记提供者（ApiContext.conversation_scopes），之后只读；没登记、或提供者对这段对话不管（返回
None）时，和没有这个功能时一样。

- 收窄只会更少：编译运行配置时（profile/compiler.py 的 ProfileNarrowing）和本人的权限、这一轮的选择取交集，
  扩展给不了本人没有的；
- 固定的回答方式：这段对话每一轮都按它，输入框里选的不算（前端取到范围就不让选）；这个人能不能用照常核对；
- 每一轮的说明不在这里，扩展经每轮补的上下文给（turn_context.py）；
- 扩展自己的资源（例如数据源）写在 resources 里（<类型>:<id>），开源这边不解读，由那类资源的扩展自己按它收窄；
- 提供者出错：这一轮不发、会话不打开（失败关闭），免得范围没收窄成、回答却按用户以为的范围说；
- 挂上：建对话时带上「挂什么」（ScopeClaim：扩展登记的类型与 id，例如专家），由登记这种类型的扩展认领（核对这个人能不能用、
  记下这段对话挂着它）。不管在哪执行（服务端、用户设备上）、从哪建（对话页、扩展页面），都经这一处挂上，之后按范围来。
  对话先建出来再挂、挂上了再编译运行配置；挂不上，对话收回。对话没建成、或删了，每个登记的扩展都撤掉它挂的（release），
  不留对不上对话的记录。
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

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
KIND = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class ScopeClaimer(Protocol):
    """扩展登记的一种能挂上的东西。"""

    async def claim(self, workspace_id: str, conversation_id: str, user_id: str, item_id: str) -> None:
        """把这段对话挂到这一项上；不存在、这个人用不了抛 ScopeRejected。"""

    async def release(self, workspace_id: str, conversation_id: str) -> None:
        """撤掉这段对话挂的（没挂的不管）：对话没建成、或删了。"""


@dataclass(frozen=True)
class ScopeClaim:
    """建对话时要挂的东西：扩展登记的类型（例如 expert）与那一项的 id。"""

    kind: str
    id: str


class ScopeUnavailable(Exception):
    """扩展说这段对话有范围，却没算出来：这一轮不发。"""


class ScopeRejected(Exception):
    """要挂的东西不认识、不存在或这个人用不了：对话不建。message 可以直接给用户看。"""


class ConversationScopes:
    def __init__(self) -> None:
        self._providers: tuple[ScopeProvider, ...] = ()
        self._claimers: dict[str, ScopeClaimer] = {}

    def add(self, provider: ScopeProvider) -> None:
        self._providers = (*self._providers, provider)

    def add_claimer(self, kind: str, claimer: ScopeClaimer) -> None:
        """扩展登记一种能在建对话时挂上的东西（装配时登记，同一种只能有一个）。"""
        if not KIND.fullmatch(kind):
            raise ValueError(f"invalid scope kind: {kind!r}")
        if kind in self._claimers:
            raise ValueError(f"scope kind already registered: {kind}")
        self._claimers = {**self._claimers, kind: claimer}

    async def claim(self, workspace_id: str, conversation_id: str, user_id: str, claim: ScopeClaim) -> None:
        """把刚建的对话挂上；没人登记这种类型也算挂不上。"""
        claimer = self._claimers.get(claim.kind)
        if claimer is None:
            raise ScopeRejected("不认识要挂的东西")
        await claimer.claim(workspace_id, conversation_id, user_id, claim.id)

    async def release(self, workspace_id: str, conversation_id: str) -> None:
        """对话没建成或删了：每个登记的扩展都撤掉它挂的。出错只记日志，不挡收回与删除。"""
        for kind, claimer in self._claimers.items():
            try:
                await claimer.release(workspace_id, conversation_id)
            except Exception:
                log.exception("releasing %s scope of conversation %s failed", kind, conversation_id)

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
