"""渠道的对话配置（channel.settings），管理页按这个结构读写。

结构就是输入框的选择（不另建「智能体」对象）：回答方式、可查的资料。回答方式是登记表里写了查哪类资源的
（conversations/answer_modes.py 的 channel）：内置两种查知识库，选在 bases；扩展的查别的（例如问数查数据源），选在
resources，两者只留回答方式用的那一样。不认人（channel.audience = public）时私聊、群聊都用渠道账号回答，能查的就是这里
选的（保存时同步成渠道账号的授权）；认人（member）时私聊按提问人本人的权限回答，这里选的只管群聊，identity_source 是
用来认人的组织来源（identity/im_identities.py）。谁能找到机器人、拉它进群，由 IM 平台的可见范围管。
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ..kernel.contract import ANSWER_MODE_PATTERN

# 写法同会话接口的知识库选择：<知识源id>.<知识库id>，或 <知识源id>.* 表示这个来源下的全部
KnowledgeBaseName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}\.([A-Za-z0-9_-]{1,64}|\*)$")]
MAX_BASES = 50
ResourceId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]  # 同权限词汇的资源 id（access/vocab.py）


class ChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_mode: str = Field(default="agent", pattern=ANSWER_MODE_PATTERN)
    bases: list[KnowledgeBaseName] = Field(default_factory=list, max_length=MAX_BASES)
    resources: list[ResourceId] = Field(default_factory=list, max_length=MAX_BASES)
    identity_source: str | None = Field(default=None, max_length=64)


def read_settings(raw: object) -> ChannelSettings:
    """库里的配置读出来；以前存的字段对不上（升级改了结构）时退回默认，不让一个渠道读坏整页。"""
    try:
        return ChannelSettings.model_validate(raw or {})
    except ValueError:
        return ChannelSettings()
