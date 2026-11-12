"""渠道的对话配置（channel.settings），管理页按这个结构读写。

结构就是输入框的选择（不另建「智能体」对象）：回答方式、可查的资料；网页渠道也可以挂一个扩展登记的对象（例如专家），
照它答（target，channels/targets.py）。回答方式是登记表里写了查哪类资源的
（conversations/answer_modes.py 的 channel）：内置两种查知识库，选在 bases；扩展的查别的（例如问数查数据源），选在
resources，两者只留回答方式用的那一样。不认人（channel.audience = public）时私聊、群聊都用渠道账号回答，能查的就是这里
选的（保存时同步成渠道账号的授权）；认人（member）时私聊按提问人本人的权限回答，这里选的只管群聊，identity_source 是
用来认人的组织来源（identity/im_identities.py）。谁能找到机器人、拉它进群，由 IM 平台的可见范围管。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..kernel.contract import ANSWER_MODE_PATTERN
from .targets import TARGET_PATTERN

# 写法同会话接口的知识库选择：<知识源id>.<知识库id>，或 <知识源id>.* 表示这个来源下的全部
KnowledgeBaseName = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}\.([A-Za-z0-9_-]{1,64}|\*)$")]
MAX_BASES = 50
ResourceId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]  # 同权限词汇的资源 id（access/vocab.py）
GroupName = Annotated[str, Field(min_length=1, max_length=100)]  # 接入方系统里组（角色）的名字
MAX_GROUP_MAP = 50


class ChannelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_mode: str = Field(default="agent", pattern=ANSWER_MODE_PATTERN)
    bases: list[KnowledgeBaseName] = Field(default_factory=list, max_length=MAX_BASES)
    resources: list[ResourceId] = Field(default_factory=list, max_length=MAX_BASES)
    identity_source: str | None = Field(default=None, max_length=64)
    # 网页嵌入的认证访问（channels/web_identity.py）：接入方系统里对不上 KnowWork 账号的人要不要自动建外部用户；
    # 接入方的组 → 用户组（外部用户每次进来按它同步这几个组的成员身份，已有成员的组在 KnowWork 里管）
    auto_create: bool = True
    group_map: dict[GroupName, ResourceId] = Field(default_factory=dict, max_length=MAX_GROUP_MAP)
    # 网页渠道挂的对象（扩展登记的，例如 expert:<id>，channels/targets.py）：挂了就照它答，回答方式存它的，资料不在这里选
    target: str | None = Field(default=None, pattern=TARGET_PATTERN)


def read_settings(raw: object) -> ChannelSettings:
    """库里的配置读出来；以前存的字段对不上（升级改了结构）时退回默认，不让一个渠道读坏整页。"""
    try:
        return ChannelSettings.model_validate(raw or {})
    except ValueError:
        return ChannelSettings()


# ---- 网页渠道（channels/web.py）----

# 允许嵌入的来源：协议加主机（可带端口），子域名可以写 https://*.example.com；不带路径。照 CSP frame-ancestors 的写法
Origin = Annotated[str, Field(pattern=r"^https?://(\*\.)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*(:\d{1,5})?$", max_length=200)]
Starter = Annotated[str, Field(min_length=1, max_length=60)]
MAX_ORIGINS = 20
MAX_STARTERS = 4


class WebOptions(BaseModel):
    """网页渠道的公开配置：放在 channel.public_config（加上发布令牌），嵌入页、嵌入脚本不登录就能读到，所以这里只放能公开的。"""

    model_config = ConfigDict(extra="forbid")

    # 允许把小窗嵌进去的网站；一个都不写就只能用分享链接整页打开，不能被别的网站嵌入
    origins: list[Origin] = Field(default_factory=list, max_length=MAX_ORIGINS)
    title: str = Field(default="", max_length=40)  # 小窗顶上的名字；空着用渠道名
    greeting: str = Field(default="", max_length=500)  # 开场白
    starters: list[Starter] = Field(default_factory=list, max_length=MAX_STARTERS)  # 快捷提问
    color: str = Field(default="", pattern=r"^(#[0-9A-Fa-f]{6})?$")  # 悬浮按钮的颜色；空着用默认
    position: Literal["right", "left"] = "right"  # 悬浮按钮在右下角还是左下角
    # 限次：每个访客（认证访问是每个人）每分钟问几次、整个渠道每天一共问几次（防刷模型费用）
    per_minute: int = Field(default=10, ge=1, le=120)
    per_day: int = Field(default=1000, ge=1, le=1_000_000)


def read_web_options(raw: object) -> WebOptions:
    """库里的公开配置读出来（多出的发布令牌不算在内）；读坏了退回默认。"""
    fields = {key: value for key, value in dict(raw or {}).items() if key in WebOptions.model_fields}
    try:
        return WebOptions.model_validate(fields)
    except ValueError:
        return WebOptions()
