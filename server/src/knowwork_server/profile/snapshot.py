"""运行配置快照：不可变、不含任何密钥，规范化 JSON 的 SHA-256 就是配置版本号。

快照描述「这个用户这一轮能用什么」：可用模型、工具（含风险等级）、技能（含摘要与版本）、可查的知识库（按知识源分组），
以及被拒的业务工具名（交给宿主收窄）。宿主看到的只有由它派生的 SessionPolicy。
"""

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..kernel.contract import SessionPolicy

SCHEMA_VERSION = "knowwork.runtime-profile.v1"
NAME_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"
MAX_SELECTED = 32


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


MAX_DROPPED = 20
MAX_PINNED = 50


class PinnedScope(_Frozen):
    """用户这一轮在一个知识库里 @ 点名的文档与标签：检索时这个库只查这几份文档、带这几个标签的文档（两者取并）。"""

    base: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}\.[A-Za-z0-9_-]{1,64}$")  # 全局 id <知识源id>.<知识库id>
    documents: tuple[Annotated[str, Field(min_length=1, max_length=300)], ...] = Field(default=(), max_length=MAX_PINNED)
    tags: tuple[Annotated[str, Field(min_length=1, max_length=100)], ...] = Field(default=(), max_length=MAX_PINNED)


def _merged_pins(pins: tuple[PinnedScope, ...], whole: tuple[str, ...] | None) -> tuple[PinnedScope, ...]:
    """同一个库的点名合起来；整个库（或整个知识源）也选了的，整库优先，去掉点名；什么都没点到的去掉。"""
    chosen = set(whole or ())
    merged: dict[str, tuple[set[str], set[str]]] = {}
    for pin in pins:
        if pin.base in chosen or f"{pin.base.partition('.')[0]}.*" in chosen:
            continue
        documents, tags = merged.setdefault(pin.base, (set(), set()))
        documents.update(pin.documents)
        tags.update(pin.tags)
    return tuple(PinnedScope(base=base, documents=tuple(sorted(d)), tags=tuple(sorted(t)))
                 for base, (d, t) in sorted(merged.items()) if d or t)


class TurnSelection(_Frozen):
    """本轮的选择。None 表示没有选（技能不标选中、知识用全部可查的），空元组表示明确选了「无」。

    知识按知识库选：全局 id `<知识源id>.<知识库id>`，或 `<知识源id>.*` 表示这个来源下的全部。
    """

    skills: tuple[str, ...] | None = Field(default=None, max_length=MAX_SELECTED)
    knowledge_bases: tuple[str, ...] | None = Field(default=None, max_length=MAX_SELECTED)
    # 用户这一轮撤掉的检索条件（检索工具给出的写法，如「部门 = 财务部」）：检索时去掉，不交给知识源
    dropped_conditions: tuple[str, ...] = Field(default=(), max_length=MAX_DROPPED)
    # 用户这一轮 @ 的文件与标签，按知识库分组（@ 整个知识库的放在 knowledge_bases）
    pins: tuple[PinnedScope, ...] = Field(default=(), max_length=MAX_SELECTED)

    def normalized(self) -> "TurnSelection":
        """排序去重；点名了文件、标签的知识库并进 knowledge_bases（这一轮只查选了的库和点名的库）。"""
        def clean(values: tuple[str, ...] | None) -> tuple[str, ...] | None:
            return None if values is None else tuple(sorted(set(values)))

        pins = _merged_pins(self.pins, self.knowledge_bases)
        bases = self.knowledge_bases
        if pins:
            bases = (*(bases or ()), *(pin.base for pin in pins))
        return TurnSelection(skills=clean(self.skills), knowledge_bases=clean(bases),
                             dropped_conditions=tuple(sorted(set(self.dropped_conditions))), pins=pins)


NO_SELECTION = TurnSelection()


class ProfileTool(_Frozen):
    name: str
    risk: Literal["read", "write", "dangerous"]
    builtin: bool


class ProfileSkill(_Frozen):
    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    summary: str = Field(max_length=1024)
    scope: str
    selected: bool = False


class ProfileKnowledgeSource(_Frozen):
    id: str
    name: str
    kind: str
    # 本人可查的知识库（知识源内的 id）；"*" 表示本人在这个知识源里读得到的全部。2026-10-20 以前的快照没有这一项
    bases: tuple[str, ...] = ()


HOST_FIELDS = ("schema_version", "workspace_id", "user_id", "role", "member", "tools", "denied_tools", "skills")


class RuntimeProfile(_Frozen):
    schema_version: Literal["knowwork.runtime-profile.v1"] = SCHEMA_VERSION
    workspace_id: str
    user_id: str
    role: str | None
    member: bool
    tools: tuple[ProfileTool, ...] = ()
    denied_tools: tuple[str, ...] = ()
    skills: tuple[ProfileSkill, ...] = ()
    knowledge: tuple[ProfileKnowledgeSource, ...] = ()
    # 本轮明确选择的知识库（selection 给了时，写法同 TurnSelection.knowledge_bases）；None = 不限定，用全部可查的。
    # 2026-10-20 以前的快照这里是知识源 id，知识工具按「这个来源下的全部」理解
    bound_knowledge: tuple[str, ...] | None = None
    # 用户这一轮撤掉的检索条件，检索工具执行时去掉（同 TurnSelection.dropped_conditions）
    dropped_conditions: tuple[str, ...] = ()
    # 用户这一轮 @ 点名的文件与标签（同 TurnSelection.pins），检索工具执行时只查它们
    pins: tuple[PinnedScope, ...] = ()

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def version(self) -> str:
        return content_version(self.payload())

    def host_payload(self) -> dict[str, Any]:
        """宿主用得到的部分：身份、工具、被拒工具、技能清单（不含本轮选中）。宿主打开会话时按它拉工具与技能、收窄工具，
        它变了才要重开宿主会话。本轮选择（选的知识库、@ 的文件与标签、撤掉的条件、技能的选中）不在这里：
        它们只在业务服务执行工具时从绑定读出来生效，知识库目录每轮随消息下发，宿主用不到。"""
        payload = self.model_dump(mode="json", include=set(HOST_FIELDS))
        payload["skills"] = [{key: value for key, value in skill.items() if key != "selected"}
                             for skill in payload["skills"]]
        return payload

    @property
    def host_version(self) -> str:
        """宿主相关部分的版本：变了才释放并重开宿主会话，「本会话内不再询问」的审批授权也按它记。"""
        return content_version(self.host_payload())

    def policy(self) -> SessionPolicy:
        """宿主侧策略：被拒的工具名。业务工具与全局工具都可以出现在这里（宿主按类分别收窄）。"""
        return SessionPolicy(deny=self.denied_tools)


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_version(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
