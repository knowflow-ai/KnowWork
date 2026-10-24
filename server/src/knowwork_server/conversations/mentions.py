"""用户在输入框里 @ 的东西：知识库、目录、文件、标签、技能。

两件事：换成本轮选择（服务端据此限定检索：@ 的知识库与目录整库查，@ 的文件、标签所在的库只查它们；技能照 DSH
官方的 /技能名 手势由宿主写进消息）；再把名字交给宿主写进这条用户消息（模型知道用户点名了什么，气泡上也显示出来）。
名字只用来显示和告诉模型，限定检索只认 id，名字被改了也越不过权限。
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..kernel.contract import TurnMention
from ..profile.snapshot import MAX_SELECTED, PinnedScope, TurnSelection

MAX_MENTIONS = 30
MentionType = Literal["knowledge_base", "folder", "document", "tag", "skill"]
BASE_ID = r"^[A-Za-z0-9_-]{1,64}\.[A-Za-z0-9_-]{1,64}$"
SKILL_NAME = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"  # 与 DSH 官方 isSkillName 相同，/名字 手势才认得
BaseId = Annotated[str, Field(pattern=BASE_ID)]


class Mention(BaseModel):
    """一个 @。id 按类型：knowledge_base 是知识库全局 id，document 是文档引用（doc_ref），tag 是标签，
    skill 是技能名，folder 是目录路径（只显示用，限定靠 bases）。"""

    model_config = ConfigDict(extra="forbid")

    type: MentionType
    id: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=200)
    base: BaseId | None = None  # document、tag 所在的知识库
    base_name: str | None = Field(default=None, max_length=200)
    bases: list[BaseId] = Field(default_factory=list, max_length=MAX_SELECTED)  # folder 下的知识库（前端按目录树展开）

    @model_validator(mode="after")
    def _shape(self) -> "Mention":
        if self.type == "knowledge_base" and not re.fullmatch(BASE_ID, self.id):
            raise ValueError("知识库 id 格式不对")
        if self.type in ("document", "tag") and self.base is None:
            raise ValueError("文件、标签要带所在的知识库")
        if self.type == "folder" and not self.bases:
            raise ValueError("目录下没有知识库")
        if self.type == "skill" and not re.fullmatch(SKILL_NAME, self.id):
            raise ValueError("技能名格式不对")
        return self


def mentioned(selection: TurnSelection, mentions: list[Mention]) -> TurnSelection:
    """把 @ 并进本轮选择。没 @ 知识库或目录时 knowledge_bases 不动（只 @ 了文件、标签时，运行配置把它们所在的库
    并进来；什么都没 @ 时照旧用全部可查的）。"""
    if not mentions:
        return selection
    whole = [m.id for m in mentions if m.type == "knowledge_base"] + [b for m in mentions if m.type == "folder"
                                                                         for b in m.bases]
    skills = [m.id for m in mentions if m.type == "skill"]
    pins = tuple(PinnedScope(base=m.base, **({"documents": (m.id,)} if m.type == "document" else {"tags": (m.id,)}))
                 for m in mentions if m.type in ("document", "tag") and m.base is not None)
    return TurnSelection(
        skills=(*(selection.skills or ()), *skills) if skills else selection.skills,
        knowledge_bases=(*(selection.knowledge_bases or ()), *whole) if whole else selection.knowledge_bases,
        dropped_conditions=selection.dropped_conditions, pins=(*selection.pins, *pins))


def turn_mentions(mentions: list[Mention]) -> tuple[TurnMention, ...]:
    """交给宿主写进消息的部分。目录下的知识库不带（重新生成时前端按目录路径重新展开），只带路径。"""
    return tuple(TurnMention(type=m.type, id=m.id, name=m.name, base=m.base,
                             base_name=(m.base_name or m.base) if m.base else None)
                 for m in mentions)
