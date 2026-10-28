"""渠道的对话配置（channel.settings），管理页按这个结构读写。

结构就是输入框的选择（不另建「智能体」对象）：回答方式、可查的知识库。IM 机器人不认人：私聊、群聊都用渠道账号
回答，能查的就是这里选的知识库（保存时同步成渠道账号的授权）。谁能找到机器人、拉它进群，由 IM 平台的可见范围管。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# 写法同会话接口的知识库选择：<知识源id>.<知识库id>，或 <知识源id>.* 表示这个来源下的全部
KnowledgeBaseName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}\.([A-Za-z0-9_-]{1,64}|\*)$")]
MAX_BASES = 50


class ChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_mode: Literal["agent", "quick"] = "agent"
    bases: list[KnowledgeBaseName] = Field(default_factory=list, max_length=MAX_BASES)


def read_settings(raw: object) -> ChannelSettings:
    """库里的配置读出来；以前存的字段对不上（升级改了结构）时退回默认，不让一个渠道读坏整页。"""
    try:
        return ChannelSettings.model_validate(raw or {})
    except ValueError:
        return ChannelSettings()
