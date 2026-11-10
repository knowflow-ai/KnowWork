"""运行配置编译：纯函数。

输入是已经按授权取好的事实（ProfileFacts），输出是不可变快照；同一输入永远得到同一版本号。
- 不是空间成员：模型、技能、知识全空，全部工具被拒；
- 工具：有使用权的列出（含风险等级），其余名字进 denied_tools 交给宿主收窄；部署层面收掉的宿主官方工具
  （例如没开沙箱时的 bash、文件工具）一并进 denied_tools；
- 技能：逐个转换，单个技能不合法只跳过并记日志，不影响其他技能；
- 知识：按知识源分组列出可查的知识库；本轮选了知识库时只保留选中且可查的，并记下选择范围
  （一个都查不了时由知识工具直接说明原因）；
- 扩展给这段对话的范围（ProfileNarrowing，conversations/scopes.py）：知识库、技能、MCP 服务收窄到范围里的。只会更少，
  和本人的权限、这一轮的选择取交集，扩展给不了本人没有的。
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from ..tools.registry import ToolSpec
from .snapshot import (
    NO_NARROWING,
    NO_SELECTION,
    ProfileKnowledgeSource,
    ProfileNarrowing,
    ProfileSkill,
    ProfileTool,
    RuntimeProfile,
    TurnSelection,
    base_in,
)

log = logging.getLogger(__name__)

MAX_SUMMARY = 1024
ALL_BASES = "*"
# MCP 工具名 mcp__<服务>__<工具>（同 tools/mcp/naming.py；那个包的初始化会绕回这里，不引它）
MCP_PREFIX = "mcp__"


@dataclass(frozen=True)
class KnowledgeEntry:
    id: str
    name: str
    kind: str
    enabled: bool = True
    bases: tuple[str, ...] = (ALL_BASES,)  # 可查的知识库（知识源内的 id），"*" = 本人在知识源里读得到的全部


@dataclass(frozen=True)
class ProfileFacts:
    """编译的全部输入。tools 是注册表里的全部工具；permitted_tools 是其中这个用户此刻有使用权的名字。"""

    workspace_id: str
    user_id: str
    role: str | None
    member: bool
    tools: tuple[ToolSpec, ...] = ()
    permitted_tools: frozenset[str] = frozenset()
    builtin_tools: frozenset[str] = frozenset()
    skills: tuple[Any, ...] = ()  # skills.views.CatalogEntry：name、version、digest、description、scope
    knowledge: tuple[KnowledgeEntry, ...] = ()
    selection: TurnSelection = field(default=NO_SELECTION)
    host_denied: tuple[str, ...] = ()
    narrowing: ProfileNarrowing | None = None


def compile_profile(facts: ProfileFacts) -> RuntimeProfile:
    narrowing = facts.narrowing or NO_NARROWING
    selection = _within(facts.selection, narrowing.knowledge_bases).normalized()
    names = sorted({spec.name for spec in facts.tools})
    if not facts.member:
        return RuntimeProfile(workspace_id=facts.workspace_id, user_id=facts.user_id, role=None, member=False,
                              denied_tools=_denied(names, facts.host_denied), bound_knowledge=selection.knowledge_bases,
                              dropped_conditions=selection.dropped_conditions, pins=selection.pins)
    tools = _tools(facts.tools, _servers_within(facts.permitted_tools, narrowing.mcp_servers), facts.builtin_tools)
    allowed = {tool.name for tool in tools}
    knowledge, bound = _knowledge(facts.knowledge, selection.knowledge_bases)
    skills = facts.skills if narrowing.skills is None else tuple(
        entry for entry in facts.skills if getattr(entry, "name", None) in narrowing.skills)
    return RuntimeProfile(
        workspace_id=facts.workspace_id, user_id=facts.user_id, role=facts.role, member=True,
        tools=tools, denied_tools=_denied((n for n in names if n not in allowed), facts.host_denied),
        skills=_skills(skills, selection.skills), knowledge=knowledge, bound_knowledge=bound,
        dropped_conditions=selection.dropped_conditions, pins=selection.pins)


def _within(selection: TurnSelection, allowed: tuple[str, ...] | None) -> TurnSelection:
    """这一轮的选择收进范围：选了的取交集；什么都没选就是范围本身；只点名了文件、标签的照原来只查点名的库
    （范围外的点名去掉）。"""
    if allowed is None:
        return selection
    pins = tuple(pin for pin in selection.pins if _allows(allowed, pin.base))
    if selection.knowledge_bases is not None:
        bases: tuple[str, ...] | None = _intersect(selection.knowledge_bases, allowed)
    else:
        bases = None if pins else allowed
    return selection.model_copy(update={"knowledge_bases": bases, "pins": pins})


def _allows(allowed: tuple[str, ...], item: str) -> bool:
    return base_in(allowed, item)


def _intersect(selected: tuple[str, ...], allowed: tuple[str, ...]) -> tuple[str, ...]:
    """选中的在范围里的留下；选了整个知识源（<知识源>.*）的换成范围里这个来源下的那几个。"""
    kept: set[str] = set()
    for item in selected:
        source, _, kb = item.partition(".")
        if _allows(allowed, item):
            kept.add(item)
        elif kb in ("", ALL_BASES):
            kept.update(a for a in allowed if a.partition(".")[0] == source)
    return tuple(sorted(kept))


def _servers_within(permitted: frozenset[str], servers: tuple[str, ...] | None) -> frozenset[str]:
    """范围外 MCP 服务的工具不给（进 denied_tools）；不是 MCP 的工具不受影响。"""
    if servers is None:
        return permitted
    prefixes = tuple(f"{MCP_PREFIX}{server}__" for server in servers)
    return frozenset(name for name in permitted if not name.startswith(MCP_PREFIX) or name.startswith(prefixes))


def _denied(names: Iterable[str], host_denied: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({*names, *host_denied}))


def _tools(specs: Iterable[ToolSpec], permitted: frozenset[str], builtin: frozenset[str]) -> tuple[ProfileTool, ...]:
    chosen = {spec.name: spec for spec in specs if spec.name in permitted}
    return tuple(ProfileTool(name=name, risk=chosen[name].risk, builtin=name in builtin) for name in sorted(chosen))




def _skills(entries: Iterable[Any], selected: tuple[str, ...] | None) -> tuple[ProfileSkill, ...]:
    picked = set(selected or ())
    compiled: dict[str, ProfileSkill] = {}
    for entry in entries:
        skill = _skill(entry, picked)
        if skill is None:
            continue
        if skill.name in compiled:
            log.warning("skill %s skipped from runtime profile: duplicate name", skill.name)
            continue
        compiled[skill.name] = skill
    missing = picked - set(compiled)
    if missing:
        log.warning("selected skills not available, ignored: %s", ", ".join(sorted(missing)))
    return tuple(compiled[name] for name in sorted(compiled))


def _skill(entry: Any, picked: set[str]) -> ProfileSkill | None:
    """一个技能编译失败只跳过它并记日志。"""
    try:
        name = str(entry.name)
        return ProfileSkill(name=name, version=str(entry.version), digest=str(entry.digest),
                            summary=str(entry.description)[:MAX_SUMMARY], scope=str(entry.scope),
                            selected=name in picked)
    except (AttributeError, TypeError, ValueError, ValidationError) as error:
        log.warning("skill %r skipped from runtime profile: %s", getattr(entry, "name", "?"), type(error).__name__)
        return None


def _knowledge(entries: Iterable[KnowledgeEntry], selected: tuple[str, ...] | None
               ) -> tuple[tuple[ProfileKnowledgeSource, ...], tuple[str, ...] | None]:
    readable = {e.id: e for e in entries if e.enabled}
    if selected is None:
        return tuple(_source(readable[key], readable[key].bases) for key in sorted(readable)), None
    picked = _picked(selected)
    kept = (_source(readable[key], _narrow(readable[key].bases, picked[key])) for key in picked if key in readable)
    return tuple(source for source in kept if source.bases), selected


def _source(entry: KnowledgeEntry, bases: tuple[str, ...]) -> ProfileKnowledgeSource:
    return ProfileKnowledgeSource(id=entry.id, name=entry.name, kind=entry.kind, bases=bases)


def _picked(selected: tuple[str, ...]) -> dict[str, frozenset[str] | None]:
    """按知识源分组的选择（写法见 TurnSelection）；None 表示这个来源下的全部。只写知识源 id 的旧写法同 <id>.*。"""
    picked: dict[str, frozenset[str] | None] = {}
    for item in selected:
        source, dot, kb = item.partition(".")
        current = picked.get(source, frozenset())
        picked[source] = None if not dot or kb == ALL_BASES or current is None else current | {kb}
    return picked


def _narrow(bases: tuple[str, ...], picked: frozenset[str] | None) -> tuple[str, ...]:
    if picked is None:
        return bases
    if ALL_BASES in bases:
        return tuple(sorted(picked))  # 选中的是否真读得到，检索时由知识源判断
    return tuple(kb for kb in bases if kb in picked)
