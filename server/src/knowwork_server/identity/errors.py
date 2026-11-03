"""身份模块的业务异常。对外提示统一、不区分具体原因，由路由层翻译成 HTTP 响应。"""


class IdentityError(Exception):
    """身份相关的业务错误基类。"""


class EmailTaken(IdentityError):
    pass


class InvalidInvitation(IdentityError):
    pass


class NotAllowed(IdentityError):
    pass


class InvalidInput(IdentityError):
    pass


class SsoFailure(IdentityError):
    """外部登录失败（单点登录、域账号、企业微信等）。具体原因只用于内部判断，不返回给调用方。"""


class OidcFailure(SsoFailure):
    """OIDC 单点登录失败。"""


class NotFound(IdentityError):
    pass


class Conflict(IdentityError):
    """操作和现状冲突，例如改环境变量配的登录方式。"""
