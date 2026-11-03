"""跳转登录的路径。路由与登录方式列表都用这里的，免得两处各写一份对不上（也免得互相导入成环）。"""

# 环境变量配的 OIDC：客户在身份中台登记过这个回调地址，改了要他们重配，不动它
OIDC_LOGIN_PATH = "/api/auth/oidc/login"
OIDC_CALLBACK_PATH = "/api/auth/oidc/callback"
SSO_PREFIX = "/api/auth/sso"


def sso_login_path(provider_id: str) -> str:
    return f"{SSO_PREFIX}/{provider_id}/login"


def sso_callback_path(provider_id: str) -> str:
    return f"{SSO_PREFIX}/{provider_id}/callback"
