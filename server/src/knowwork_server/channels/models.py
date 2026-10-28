"""渠道：企业微信、飞书、钉钉机器人与网页分享这类对外对话入口。都按工作空间做行级安全。

- channel：一个入口。凭据里不保密的部分放 public_config（bot_id：企业微信的 Bot ID、飞书的 App ID、钉钉的 Client ID），
  保密的部分（各家的 Secret）加密后放 secret；
  settings 是对话配置（回答方式、可查的知识库，见 settings.py）；status / last_error 是连接状态，管理页显示。
  每个渠道有一个同 id 的渠道账号（user.kind = channel），IM 里的提问都用它的身份回答，能查的就是授给它的知识库；
- channel_session：渠道里的一个对话位置（私聊的人、群里的人）当前对应哪个会话，发「新会话」时换掉；
  display_name 记下提问人在平台上的名字，管理页看谁问过。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)


class Channel(Base):
    __tablename__ = "channel"

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    platform: Mapped[str] = mapped_column(String(16))  # wecom / feishu / dingtalk（以后 web）
    audience: Mapped[str] = mapped_column(String(16), default="public")  # public 用渠道账号回答（IM 都是）
    name: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    public_config: Mapped[dict] = mapped_column(JSONB, default=dict)
    secret: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="stopped")  # stopped / connecting / connected / error
    last_error: Mapped[str] = mapped_column(String(500), default="")
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(ID)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChannelSession(Base):
    __tablename__ = "channel_session"

    channel_id: Mapped[str] = mapped_column(ForeignKey("channel.id", ondelete="CASCADE"), primary_key=True)
    session_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ID, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ID)
    display_name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


CHANNEL_TABLES = ("channel", "channel_session")
