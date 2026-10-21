"""运行配置快照（技术方案 5.3 节）：不可变、不含任何密钥，规范化 JSON 的 SHA-256 就是配置版本号。

快照描述「这个用户这一轮能用什么」：可用模型、工具（含风险等级）、技能（含摘要与版本）、可查的知识库（按知识源分组），
以及被拒的业务工具名（交给宿主收窄）。宿主看到的只有由它派生的 SessionPolicy。
"""

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..kernel.contract import SessionPolicy

SCHEMA_VERSION = "knowwork.runtime-profile.v1"
NAME_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"
MAX_SELECTED = 32


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TurnSelection(_Frozen):
    """本轮的选择。None 表示没有选（技能不标选中、知识用全部可查的），空元组表示明确选了「无」。

    知识按知识库选：全局 id `<知识源id>.<知识库id>`，或 `<知识源id>.*` 表示这个来源下的全部（技术方案 8.1 节）。
    """

    skills: tuple[str, ...] | None = Field(default=None, max_length=MAX_SELECTED)
    knowledge_bases: tuple[str, ...] | None = Field(default=None, max_length=MAX_SELECTED)

    def normalized(self) -> "TurnSelection":
        def clean(values: tuple[str, ...] | None) -> tuple[str, ...] | None:
            return None if values is None else tuple(sorted(set(values)))

        return TurnSelection(skills=clean(self.skills), knowledge_bases=clean(self.knowledge_bases))


NO_SELECTION = TurnSelection()


class ProfileModel(_Frozen):
    id: str
    alias: str
    name: str


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


class RuntimeProfile(_Frozen):
    schema_version: Literal["knowwork.runtime-profile.v1"] = SCHEMA_VERSION
    workspace_id: str
    user_id: str
    role: str | None
    member: bool
    models: tuple[ProfileModel, ...] = ()
    tools: tuple[ProfileTool, ...] = ()
    denied_tools: tuple[str, ...] = ()
    skills: tuple[ProfileSkill, ...] = ()
    knowledge: tuple[ProfileKnowledgeSource, ...] = ()
    # 本轮明确选择的知识库（selection 给了时，写法同 TurnSelection.knowledge_bases）；None = 不限定，用全部可查的。
    # 2026-10-20 以前的快照这里是知识源 id，知识工具按「这个来源下的全部」理解
    bound_knowledge: tuple[str, ...] | None = None

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def version(self) -> str:
        return content_version(self.payload())

    def policy(self) -> SessionPolicy:
        """宿主侧策略：被拒的工具名。业务工具与全局工具都可以出现在这里（宿主按类分别收窄）。"""
        return SessionPolicy(deny=self.denied_tools)


def canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_version(payload: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
