"""知识库的全局 id、选择与第 1 层收窄（技术方案第 8 章开头、5.4 节）。

- 全局 id 是 `<知识源id>.<知识库id>`，授权资源写成 `knowledge_base:<全局 id>`，`<知识源id>.*` 表示这个来源下的全部；
- 本轮选择与工具参数用同样的写法；旧快照里只写了知识源 id 的，当作这个来源下的全部；
- 第 1 层收窄只用 KnowWork 自己知道的事：授权、知识源的开放范围、本轮选择。KnowFlow 的权限是第 2 层，由连接器带着
  调用者身份去查时生效。
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..access.engine import VisibleSet
from .errors import InvalidInput

RESOURCE = "knowledge_base"
ALL = "*"
_PART = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def base_id(source_id: str, kb_id: str) -> str:
    return f"{source_id}.{kb_id}"


def parse_selector(text: str) -> tuple[str, str]:
    """`s.k` → (s, k)；`s.*` 与只写知识源 id 的旧写法 → (s, "*")。"""
    source, dot, kb = (text or "").strip().partition(".")
    kb = kb if dot else ALL
    if not _PART.fullmatch(source) or not (kb == ALL or _PART.fullmatch(kb)):
        raise InvalidInput(f"知识库「{text}」的写法不对，应为 <知识源id>.<知识库id>")
    return source, kb


@dataclass(frozen=True)
class Selection:
    """按知识源分组的知识库选择；某个来源的值为 None 表示这个来源下的全部。"""

    by_source: Mapping[str, frozenset[str] | None]

    @staticmethod
    def of(items: Iterable[str]) -> "Selection":
        grouped: dict[str, frozenset[str] | None] = {}
        for item in items:
            source, kb = parse_selector(item)
            current = grouped.get(source, frozenset())
            grouped[source] = None if kb == ALL or current is None else current | {kb}
        return Selection(grouped)

    def covers(self, source_id: str, kb_id: str) -> bool:
        if source_id not in self.by_source:
            return False
        picked = self.by_source[source_id]
        return picked is None or kb_id in picked

    def outside(self, bound: "Selection") -> str | None:
        """本选择里第一个不在 bound 范围内的知识库（全局 id），都在范围内返回 None。"""
        for source, picked in self.by_source.items():
            allowed = bound.by_source.get(source, frozenset())
            if allowed is None:
                continue
            if picked is None:
                return base_id(source, ALL)
            extra = sorted(picked - allowed)
            if extra:
                return base_id(source, extra[0])
        return None


@dataclass(frozen=True)
class Grants:
    """调用者在知识库上的使用授权（access 的可见集合，id 是全局 id 或 `<知识源id>.*`）。"""

    visible: VisibleSet

    def allows(self, source_id: str, kb_id: str) -> bool:
        return self.visible.allows(base_id(source_id, kb_id))

    def whole(self, source_id: str) -> bool:
        """授权覆盖这个来源下的全部知识库。"""
        return self.visible.all_ or base_id(source_id, ALL) in self.visible.ids

    def any_in(self, source_id: str) -> bool:
        return self.whole(source_id) or any(item.startswith(f"{source_id}.") for item in self.visible.ids)

    def explicit(self, source_id: str) -> tuple[str, ...]:
        """单独授权的知识库（知识源内的 id），不含通配。"""
        prefix = f"{source_id}."
        return tuple(sorted(item[len(prefix):] for item in self.visible.ids
                            if item.startswith(prefix) and item != base_id(source_id, ALL)))

    def within(self, source_id: str, configured: tuple[str, ...]) -> tuple[str, ...]:
        """不问知识源就能写下的可查范围：开放范围里授权了的；开放范围留空时是单独授权的知识库，
        授权又覆盖整个来源时写成 ("*",)，表示「本人在知识源里读得到的全部」。给运行配置快照用。"""
        if configured:
            return tuple(kb for kb in configured if self.allows(source_id, kb))
        return (ALL,) if self.whole(source_id) else self.explicit(source_id)


def candidates(source_id: str, configured: tuple[str, ...], grants: Grants,
               picked: frozenset[str] | None) -> tuple[str, ...] | None:
    """不发请求就能确定的候选知识库；返回 None 表示开放范围留空、授权又覆盖整个来源，要以调用者身份列出来。"""
    if picked is not None:
        return tuple(sorted(picked))
    if configured:
        return configured
    return None if grants.whole(source_id) else grants.explicit(source_id)


def admit(source_id: str, kbs: Iterable[str], configured: tuple[str, ...],
          grants: Grants) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """候选过一遍开放范围与授权，返回（放行的，挡下的）。"""
    kept: list[str] = []
    refused: list[str] = []
    for kb in kbs:
        ok = (not configured or kb in configured) and grants.allows(source_id, kb)
        (kept if ok else refused).append(kb)
    return tuple(kept), tuple(refused)
