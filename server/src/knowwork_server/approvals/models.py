"""会话审批授权：用户批准时勾了「本会话内同类操作不再询问」，记一条。

范围键 = 对话 + 用户 + 工具名 + 当时的运行配置版本。管理员改了工具的风险等级、审批方式或用户的权限，
运行配置版本随之变化，旧授权自然不再生效，下一次重新问。对话删除时级联删除。
带 workspace_id，开启行级安全（登记在 db/registry.RLS_TABLES）。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

ID = String(64)


class ApprovalGrant(Base):
    __tablename__ = "approval_grant"
    __table_args__ = (UniqueConstraint("conversation_id", "user_id", "tool_name", "profile_version",
                                       name="uk_approval_grant_scope"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspace.id", ondelete="CASCADE"), index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversation.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ID)
    tool_name: Mapped[str] = mapped_column(String(64))
    profile_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
