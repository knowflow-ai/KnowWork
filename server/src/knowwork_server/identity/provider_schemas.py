"""后台「登录方式」的请求体与各类型的设置校验（OIDC、通用 OAuth2）。字段名用下划线，同系统设置的其他接口。"""

import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .errors import InvalidInput

LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# 非对称签名算法；none 与 HS*（把公钥当 HMAC 密钥的攻击）一律不收，同 OidcConfig 的检查
ASYMMETRIC_ALGORITHMS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512",
                                   "ES256", "ES384", "ES512", "EdDSA"})
_PATH = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*$")
_SCOPE = re.compile(r"^[A-Za-z0-9_:./\-]{1,100}$")

Kind = Literal["oidc", "oauth2"]
Signup = Literal["known_only", "auto"]
Url = Annotated[str, Field(min_length=1, max_length=2048)]


def check_url(value: str) -> str:
    """https，或本机地址的 http（先在本机试一下）；地址里不能带账号密码与片段。"""
    value = value.strip()
    parts = urlsplit(value)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError("地址格式不正确")
    if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
        raise ValueError("地址必须是 https（本机地址除外）")
    if parts.username or parts.password or parts.fragment:
        raise ValueError("地址里不能带账号密码或 #")
    return value


def _scopes(values: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values if value.strip()]
    if any(not _SCOPE.match(value) for value in cleaned):
        raise ValueError("scope 只能是字母、数字和 _:./-")
    return list(dict.fromkeys(cleaned))


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OidcSettings(_Strict):
    issuer: Annotated[str, Field(min_length=1, max_length=512)]
    client_id: Annotated[str, Field(min_length=1, max_length=255)]
    scopes: Annotated[list[str], Field(max_length=20)] = ["openid", "email", "profile"]
    algorithms: Annotated[list[str], Field(min_length=1, max_length=10)] = ["RS256"]
    # 有的企业身份中台不给 email_verified；确认邮箱由企业统一分配后才打开
    trust_unverified_email: bool = False

    _issuer = field_validator("issuer")(check_url)

    @field_validator("scopes")
    @classmethod
    def _openid(cls, values: list[str]) -> list[str]:
        cleaned = _scopes(values)
        if "openid" not in cleaned:
            raise ValueError("OIDC 的 scope 必须包含 openid")
        return cleaned

    @field_validator("algorithms")
    @classmethod
    def _asymmetric(cls, values: list[str]) -> list[str]:
        if any(value not in ASYMMETRIC_ALGORITHMS for value in values):
            raise ValueError("签名算法只能是非对称算法（如 RS256、ES256）")
        return list(dict.fromkeys(values))


class OAuth2Settings(_Strict):
    authorize_url: Url
    token_url: Url
    userinfo_url: Url
    client_id: Annotated[str, Field(min_length=1, max_length=255)]
    scopes: Annotated[list[str], Field(max_length=20)] = []
    token_auth: Literal["basic", "post"] = "basic"     # 客户端凭证放 Basic 头还是表单
    pkce: bool = True                                  # 对方不支持 PKCE 时关掉
    userinfo_token: Literal["header", "query"] = "header"  # 令牌放 Bearer 头还是查询参数
    # 用户信息响应里取值的点分路径，数字段取数组下标：data.user.id
    subject_path: Annotated[str, Field(min_length=1, max_length=200)] = "id"
    name_path: Annotated[str, Field(min_length=1, max_length=200)] = "name"
    email_path: Annotated[str, Field(min_length=1, max_length=200)] = "email"
    # OAuth2 没有 email_verified：确认邮箱由企业统一分配后才打开，否则邮箱不参与认人
    email_trusted: bool = False

    _urls = field_validator("authorize_url", "token_url", "userinfo_url")(check_url)
    _scope = field_validator("scopes")(_scopes)

    @field_validator("subject_path", "name_path", "email_path")
    @classmethod
    def _path(cls, value: str) -> str:
        if not _PATH.match(value.strip()):
            raise ValueError("字段路径写成 data.user.id 这样，用点分开")
        return value.strip()


SETTINGS: dict[str, type[_Strict]] = {"oidc": OidcSettings, "oauth2": OAuth2Settings}


def settings_for(kind: str, config: dict) -> dict:
    """按类型校验设置，返回规范化后的字典；不合格抛 InvalidInput（第一条原因给人看）。"""
    model = SETTINGS.get(kind)
    if model is None:
        raise InvalidInput("不支持的登录方式类型")
    try:
        return model.model_validate(config).model_dump()
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        message = str(first.get("msg", "设置不正确")).removeprefix("Value error, ")
        raise InvalidInput(f"{where}：{message}" if where else message) from error


Name = Annotated[str, Field(min_length=1, max_length=40)]
Secret = Annotated[str, Field(min_length=1, max_length=2048)]


class ProviderCreate(_Strict):
    kind: Kind
    name: Name
    enabled: bool = True
    signup: Signup = "known_only"
    config: dict
    client_secret: Secret


class ProviderUpdate(_Strict):
    """不传的字段保持原样；config 传了就整份替换（按类型重新校验）；client_secret 不传保持原密钥。"""

    name: Name | None = None
    enabled: bool | None = None
    signup: Signup | None = None
    config: dict | None = None
    client_secret: Secret | None = None


# 客户端密钥发往哪里：OIDC 发往发行方（发现文档里的令牌接口），OAuth2 发往令牌地址。修改时改了它又没重填密钥就拒绝，
# 否则改个地址就能把库里存的密钥发到别处（密钥只写不读）
SECRET_TARGETS = {"oidc": ("issuer",), "oauth2": ("token_url",)}


def secret_target(kind: str, config: dict) -> tuple:
    return tuple(str(config.get(key, "")).rstrip("/").lower() for key in SECRET_TARGETS.get(kind, ()))
