"""身份模块配置。由主线从全局 Settings 组装，模块内不读环境变量。"""

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...] = ("openid", "email", "profile")
    # 显式指定可接受的签名算法，绝不接受 none 或 HS*（防止把公钥当 HMAC 密钥）。
    algorithms: tuple[str, ...] = ("RS256",)
    # 只允许已存在或已被邀请的用户登录；关闭后首次登录自动建号。
    allow_only_known_users: bool = True
    # 按 email 匹配本地账号前要求 email_verified 为真；有的提供方（如部分企业 IdP）不给该声明，
    # 确认其邮箱由企业统一分配后才可打开。
    trust_unverified_email: bool = False
    post_login_redirect: str = "/"
    state_ttl: timedelta = timedelta(minutes=10)
    leeway_seconds: int = 60

    def __post_init__(self) -> None:
        unsafe = [alg for alg in self.algorithms if alg.lower() == "none" or alg.upper().startswith("HS")]
        if not self.algorithms or unsafe:
            raise ValueError("OIDC 签名算法必须显式指定为非对称算法")


@dataclass(frozen=True)
class IdentityConfig:
    public_base_url: str = "http://localhost"
    session_cookie_name: str = "kw_session"
    cookie_secure: bool = True
    session_ttl: timedelta = timedelta(days=7)
    session_sliding: bool = False
    invitation_ttl: timedelta = timedelta(days=3)
    invite_path: str = "/invite"
    oidc_state_cookie_name: str = "kw_oidc"
    oidc: OidcConfig | None = None
    login_failure_window: timedelta = timedelta(minutes=15)
    login_max_failures_per_account: int = 5
    login_max_failures_per_ip: int = 30
    # API Key 的最后使用时间最多每隔这么久写一次库，避免每个请求都写。
    api_key_touch_interval: timedelta = timedelta(seconds=60)
