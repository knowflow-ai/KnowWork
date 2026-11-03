"""身份相关的表。所有令牌类字段只存 SHA-256 摘要。"""

from datetime import datetime

from sqlalchemy import ARRAY, Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base

USER_ACTIVE = "active"
USER_DISABLED = "disabled"
# 账号种类：person 是人；channel 是渠道账号（IM 机器人用它的身份回答，没有密码、不能登录、不在成员与选人列表里）
USER_PERSON = "person"
USER_CHANNEL = "channel"


class User(Base):
    __tablename__ = "user"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="ck_user_email_lower"),
        CheckConstraint("status in ('active', 'disabled')", name="ck_user_status"),
        CheckConstraint("kind in ('person', 'channel')", name="ck_user_kind"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # 有值时唯一；企业微信等目录来的人多半拿不到邮箱，留空，不编假邮箱（没有邮箱的人不能用邮箱密码登录）
    email: Mapped[str | None] = mapped_column(String(320), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(16), default=USER_ACTIVE)
    # 谁停用的：manual 管理员（或渠道删除），directory 组织同步判定离职。同步停用的人重新在职时可以自动恢复，手工停用的不行
    disabled_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default=USER_PERSON, server_default=USER_PERSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSession(Base):
    """浏览器会话：Cookie 里是随机令牌，库里只有它的摘要。"""

    __tablename__ = "auth_session"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    auth_method: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Invitation(Base):
    __tablename__ = "auth_invitation"
    __table_args__ = (Index("ix_auth_invitation_email", "email"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str] = mapped_column(String(320))
    invited_by: Mapped[str] = mapped_column(String(32))
    # 接受后进哪个空间、当什么角色（身份模块不解释它们，建号后交给装配方）
    workspace_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiKey(Base):
    """个人 API Key（决策 K7）：只存前缀与整串的 SHA-256。"""

    __tablename__ = "api_key"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(8), unique=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list[str]] = mapped_column(ARRAY(String(64)))
    # 设计稿 5.1：Key 绑定工作空间；第一阶段可空，由 RBAC 线接入后强制。
    workspace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginState(Base):
    """一次跳转登录的状态：state、nonce、code_verifier 都只存摘要，明文在浏览器的短期 Cookie 与 URL 里。
    method 记下是哪种登录方式发的，回调时核对，一种方式的 state 不能拿到另一种方式的回调里用。"""

    __tablename__ = "login_state"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    method: Mapped[str] = mapped_column(String(64))
    nonce_hash: Mapped[str] = mapped_column(String(64))
    verifier_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExternalIdentity(Base):
    """外部身份与本地用户的绑定：realm 是外部 ID 唯一的范围（OIDC 的发行方地址、企业微信的 wecom:<CorpID>、
    LDAP 的 ldap:<来源 ID> 等），subject 是那边的 ID。一个人可以有多条（AD 一条、企业微信一条）。
    同一个企业微信企业的登录、通讯录同步与 IM 机器人用同一个 realm，天然对得上。"""

    __tablename__ = "external_identity"

    realm: Mapped[str] = mapped_column(String(512), primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    # 由哪个目录来源同步来的；单点登录建立的为空
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 账号名（AD 的 sAMAccountName、企业微信的 userid 等），跨来源对人时用
    account: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 同一平台的其他 ID（例如飞书的 open_id、union_id），IM 消息里带的往往是这些
    aliases: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    # 这个来源里他还在不在职（不在快照里、被禁用都算不在）。任一来源判离职就停用账号，所有来源都在职才恢复。
    # 登录建立的身份恒为在职
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginProvider(Base):
    """后台「登录方式」里配置的 OIDC 与通用 OAuth2。环境变量配的 OIDC 不在这里（只读显示）。
    客户端密钥加密存（secret_ciphertext），接口只给末四位；config 是各类型自己的设置，保存时按类型校验。"""

    __tablename__ = "login_provider"
    __table_args__ = (
        CheckConstraint("kind in ('oidc', 'oauth2')", name="ck_login_provider_kind"),
        CheckConstraint("signup in ('known_only', 'auto')", name="ck_login_provider_signup"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # known_only：只允许已有账号（含被邀请的）；auto：首次登录自动建号
    signup: Mapped[str] = mapped_column(String(16), default="known_only")
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    secret_last4: Mapped[str] = mapped_column(String(4), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
