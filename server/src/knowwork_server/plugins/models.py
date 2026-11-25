"""插件的登记。都按工作空间做行级安全。

- plugin_installation：一个空间里装了什么。owner_user_id 为空串是空间级（预置插件的档位、空间上传的插件），
  填了人是这个人自己装的个人插件。预置插件没有行就是「关闭」。上传相关的字段（版本、压缩包摘要、适用于、能力、工具、
  状态）是上传的插件用的；标题、说明文档、组件清单是上传时从包里读出来给插件页看的（同 DSH 官方插件页）；
- plugin_switch：一个人对「可选」插件的开关（没有行就是没开）；
- plugin_workspace_setting：空间的管控开关（允许成员安装个人插件、禁用清单）。没有行就是默认值。
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)

MODE_OFF, MODE_ON, MODE_OPTIONAL = "off", "on", "optional"
MODES = (MODE_OFF, MODE_ON, MODE_OPTIONAL)
SOURCE_BUILTIN, SOURCE_UPLOAD = "builtin", "upload"
TARGET_CLOUD, TARGET_DEVICE = "cloud", "device"  # 适用于：云端对话、本机对话（桌面端）
TARGETS = (TARGET_CLOUD, TARGET_DEVICE)
# 上传的插件：检查中（开了专属宿主时上传后先试装，plugins/trial_job.py）、可用、没通过（原因在 status_reason）
STATUS_CHECKING, STATUS_READY, STATUS_FAILED = "checking", "ready", "failed"


class PluginInstallation(Base):
    __tablename__ = "plugin_installation"
    __table_args__ = (UniqueConstraint("workspace_id", "owner_user_id", "plugin", name="uq_plugin_installation"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    owner_user_id: Mapped[str] = mapped_column(ID, default="")
    plugin: Mapped[str] = mapped_column(String(128))  # 预置插件的键，或上传包的名字
    source: Mapped[str] = mapped_column(String(16), default=SOURCE_BUILTIN)
    # 空间级的档：off 关闭、on 全员（或授权范围内默认）启用、optional 成员自己决定
    mode: Mapped[str] = mapped_column(String(16), default=MODE_OFF)
    version: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(String(500), default="")
    title: Mapped[str] = mapped_column(String(80), default="", server_default="")  # 作者写的标题，没写为空
    detail: Mapped[str] = mapped_column(Text, default="", server_default="")  # 说明文档（Markdown）
    # 组件清单：[{id, module, title, description}]，见 archive.Component
    components: Mapped[list[dict]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    archive_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    # 空间插件「给谁用」收窄了没有：没收窄是全空间；收窄后看资源授权（plugin:<id> 的 use）。同企业 MCP 的做法
    narrowed: Mapped[bool] = mapped_column(Boolean, default=False)
    archive_digest: Mapped[str | None] = mapped_column(String(64))
    targets: Mapped[list[str]] = mapped_column(JSONB, server_default=text("""'["cloud", "device"]'::jsonb"""))
    capabilities: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    tool_names: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), default="ready")
    status_reason: Mapped[str] = mapped_column(String(500), default="")
    # 个人插件：本人提交给管理员的时间（管理员转成空间插件后清掉）；没提交过为空
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[str | None] = mapped_column(ID)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class PluginSwitch(Base):
    __tablename__ = "plugin_switch"

    workspace_id: Mapped[str] = mapped_column(ID, primary_key=True)
    user_id: Mapped[str] = mapped_column(ID, primary_key=True)
    plugin: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class PluginWorkspaceSetting(Base):
    __tablename__ = "plugin_workspace_setting"

    workspace_id: Mapped[str] = mapped_column(ID, primary_key=True)
    allow_personal: Mapped[bool] = mapped_column(Boolean, default=True)
    allow_personal_device: Mapped[bool] = mapped_column(Boolean, default=True)
    blocked: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    updated_by: Mapped[str | None] = mapped_column(ID)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
