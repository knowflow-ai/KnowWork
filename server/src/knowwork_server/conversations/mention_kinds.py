"""扩展登记的 @ 类型（例如问数的「数据源」）：输入框 @ 菜单里多一组，选了以后发消息时交给扩展。扩展在业务服务启动时登记，
之后只读；没登记时菜单与发消息都和没有这个功能时一样。

内置的六种（mentions.py）效果由会话层自己做；扩展的这类效果由扩展做，会话层只管三件事：
- 列候选：options 给这个人此刻能 @ 的（菜单用）；某个扩展出错就不列它那一组，不连累别的；
- 发消息时核对：kind 没登记、id 不在 options 里的整条拒收（@ 不能越过扩展自己的权限），名字以 options 给的为准
  （名字只用来显示和告诉模型），类型名按登记的填；
- 这一轮开始前回调 before_turn（回答方式的指令算出来之前），例如问数把会话绑定到这个数据源；出错这一轮不发，
  免得范围没换成、回答却按用户以为的范围说。
answer_mode 是这类 @ 在哪种回答方式里效果最好：用户在别的方式里 @ 了它，输入框上方提示可以切过去，不强制。
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from ..kernel.contract import ANSWER_MODE_PATTERN, MENTION_KIND_PATTERN, TurnMention

log = logging.getLogger(__name__)

KIND = re.compile(MENTION_KIND_PATTERN)
MODE = re.compile(ANSWER_MODE_PATTERN)
BUILT_IN = frozenset({"knowledge_base", "folder", "document", "tag", "mcp", "skill"})
MAX_LABEL, MAX_EFFECT = 10, 40
MAX_OPTIONS = 200  # 一组最多列这么多；再多的靠输入框里打字筛（同知识库）
OPTIONS_TIMEOUT = 3.0


@dataclass(frozen=True)
class MentionOption:
    id: str
    name: str
    description: str = ""  # 菜单里名字下面那行小字


@dataclass(frozen=True)
class MentionTurn:
    """这一轮 @ 的、属于这个扩展类型的那几项（已核对过）。"""

    workspace_id: str
    user_id: str
    conversation_id: str
    answer_mode: str
    items: tuple[MentionOption, ...]


@dataclass(frozen=True)
class MentionKind:
    kind: str
    label: str  # 类型名：菜单分组、小标签上写在名字前面，也写进消息告诉模型
    effect: str  # 选了会怎样：菜单分组与小标签的悬停提示
    options: Callable[[str, str], Awaitable[Sequence[MentionOption]]]  # （工作空间，用户）→ 能 @ 的
    before_turn: Callable[[MentionTurn], Awaitable[None]] | None = None
    answer_mode: str | None = None
    # （工作空间，用户，对话）→ 这段对话里能 @ 的：扩展给对话定了范围时（scopes.py 的 resources）由扩展自己按范围收窄，
    # 它自己认得范围里的资源和候选怎么对应；不给就同 options
    scoped: Callable[[str, str, str], Awaitable[Sequence[MentionOption]]] | None = None


class MentionRejected(Exception):
    """@ 了没登记的类型，或这个人不能 @ 的那一项。"""


class MentionKinds:
    def __init__(self, *, timeout: float = OPTIONS_TIMEOUT) -> None:
        self._kinds: dict[str, MentionKind] = {}
        self._timeout = timeout

    def add(self, kind: MentionKind) -> None:
        if not KIND.fullmatch(kind.kind) or kind.kind in BUILT_IN:
            raise ValueError(f"invalid mention kind: {kind.kind!r}")
        if kind.kind in self._kinds:
            raise ValueError(f"mention kind already registered: {kind.kind}")
        if not 0 < len(kind.label) <= MAX_LABEL or len(kind.effect) > MAX_EFFECT:
            raise ValueError(f"mention kind {kind.kind} needs a short label and effect")
        if kind.answer_mode is not None and not MODE.fullmatch(kind.answer_mode):
            raise ValueError(f"mention kind {kind.kind} has an invalid answer mode")
        self._kinds = {**self._kinds, kind.kind: kind}

    def __bool__(self) -> bool:
        return bool(self._kinds)

    async def listing(self, workspace_id: str, user_id: str, conversation_id: str | None = None) -> list[dict]:
        """@ 菜单里扩展的几组（按登记顺序）；给了对话就按这段对话的范围（MentionKind.scoped）。出错或超时的那组不列。"""
        groups = await asyncio.gather(*(self._group(kind, workspace_id, user_id, conversation_id)
                                        for kind in self._kinds.values()))
        return [group for group in groups if group is not None]

    async def _group(self, kind: MentionKind, workspace_id: str, user_id: str,
                     conversation_id: str | None = None) -> dict | None:
        if conversation_id is not None and kind.scoped is not None:
            options = await self._guarded(kind, kind.scoped(workspace_id, user_id, conversation_id), workspace_id)
        else:
            options = await self._options(kind, workspace_id, user_id)
        if options is None:
            return None
        return {"kind": kind.kind, "label": kind.label, "effect": kind.effect, "answer_mode": kind.answer_mode,
                "items": [{"id": o.id, "name": o.name, "description": o.description} for o in options[:MAX_OPTIONS]]}

    async def _options(self, kind: MentionKind, workspace_id: str, user_id: str) -> Sequence[MentionOption] | None:
        return await self._guarded(kind, kind.options(workspace_id, user_id), workspace_id)

    async def _guarded(self, kind: MentionKind, pending: Awaitable[Sequence[MentionOption]],
                       workspace_id: str) -> Sequence[MentionOption] | None:
        try:
            return await asyncio.wait_for(pending, self._timeout)
        except Exception:
            log.warning("mention options of %s unavailable for workspace %s", kind.kind, workspace_id, exc_info=True)
            return None

    async def before_turn(self, *, workspace_id: str, user_id: str, conversation_id: str, answer_mode: str,
                          mentions: tuple[TurnMention, ...]) -> tuple[TurnMention, ...]:
        """核对这一轮扩展的 @、交给扩展，返回补好名字与类型名的 @ 清单。没有扩展的 @ 时原样返回。"""
        extension = [m for m in mentions if m.type == "extension"]
        if not extension:
            return mentions
        chosen = {name: await self._checked(name, [m.id for m in extension if m.kind == name], workspace_id, user_id)
                  for name in dict.fromkeys(str(m.kind) for m in extension)}
        for name, items in chosen.items():
            kind = self._kinds[name]
            if kind.before_turn is not None:
                await kind.before_turn(MentionTurn(workspace_id=workspace_id, user_id=user_id,
                                                   conversation_id=conversation_id, answer_mode=answer_mode,
                                                   items=tuple(items.values())))
        return tuple(self._named(m, chosen) for m in mentions)

    async def _checked(self, name: str, ids: list[str], workspace_id: str,
                       user_id: str) -> dict[str, MentionOption]:
        kind = self._kinds.get(name)
        if kind is None:
            raise MentionRejected("不认识这种 @")
        options = await self._options(kind, workspace_id, user_id)
        if options is None:
            raise MentionRejected(f"暂时核对不了 @ 的{kind.label}，请稍后再试")
        known = {option.id: option for option in options}
        if any(i not in known for i in ids):
            raise MentionRejected(f"没有这个{kind.label}，或你没有权限用它")
        return {i: known[i] for i in ids}

    def _named(self, mention: TurnMention, chosen: dict[str, dict[str, MentionOption]]) -> TurnMention:
        if mention.type != "extension":
            return mention
        option = chosen[str(mention.kind)][mention.id]
        return mention.model_copy(update={"name": option.name, "label": self._kinds[str(mention.kind)].label})
