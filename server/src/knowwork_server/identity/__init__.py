"""账号与身份：用户、浏览器会话、邀请注册、API Key、OIDC 单点登录。

对外只用三样：`build_identity(...)` 装配、`Identity.get_principal` / `Identity.require_scopes(...)` 依赖、
`identity.api.build_router(...)` 路由。
"""

from .config import IdentityConfig, OidcConfig
from .container import Identity, build_identity

__all__ = ["Identity", "IdentityConfig", "OidcConfig", "build_identity"]
