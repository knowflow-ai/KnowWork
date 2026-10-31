"""MCP 管理接口的入参与出参（字段用 camelCase）。出参不含请求头明文，只有名字与末四位。"""

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from .catalog import CatalogTool
from .naming import SERVER_NAME_PATTERN, server_tool_pattern
from .store import McpServerRecord

MAX_HEADERS = 10
MAX_HEADER_VALUE = 4096
# 允许下划线：HTTP 本身允许，也有服务这么起名（例如 CONTEXT7_API_KEY）
HEADER_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
# 由 HTTP 客户端与 MCP 协议自己管理的头，不许配置
RESERVED_HEADERS = frozenset({"host", "content-length", "content-type", "accept", "connection", "transfer-encoding",
                              "mcp-session-id", "mcp-protocol-version", "last-event-id"})
URL = re.compile(r"https?://[^\s/?#]+(/[^\s?#]*)?")


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


def _title(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= 100:
        raise ValueError("名称长度须为 1 到 100 个字符")
    return cleaned


def _url(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) > 1000 or not URL.fullmatch(cleaned):
        raise ValueError("地址必须是 http(s):// 开头的完整地址，且不带查询参数；凭据请放在请求头里")
    return cleaned


def _headers(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if len(value) > MAX_HEADERS:
        raise ValueError(f"请求头最多 {MAX_HEADERS} 个")
    cleaned: dict[str, str] = {}
    for name, raw in value.items():
        if not HEADER_NAME.match(name) or name.lower() in RESERVED_HEADERS:
            raise ValueError(f"请求头 {name[:64]} 的名字不合法或不允许配置")
        if name.lower() in {key.lower() for key in cleaned}:
            raise ValueError(f"请求头 {name} 重复")
        text = raw.strip()
        if not text or len(text) > MAX_HEADER_VALUE or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
            raise ValueError(f"请求头 {name} 的值为空、过长或含控制字符")
        cleaned[name] = text
    return cleaned


class _ServerFields(_Model):
    """新建与修改共用的字段校验；修改时字段可以缺省（None 跳过校验）。"""

    @field_validator("title", check_fields=False)
    @classmethod
    def _check_title(cls, value: str | None) -> str | None:
        return _title(value)

    @field_validator("url", check_fields=False)
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return _url(value)

    @field_validator("headers", check_fields=False)
    @classmethod
    def _check_headers(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _headers(value)


def _token_text(value: str) -> str:
    text = value.strip()
    if not text or len(text) > MAX_HEADER_VALUE or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError("令牌为空、过长或含控制字符")
    return text


def _token(value: str | None) -> str | None:
    return None if value is None else _token_text(value)


class TokenConfig(_Model):
    """个人访问令牌：调用时带 `header: prefix + 令牌`；help 是给成员看的「去哪拿令牌」。"""

    header: str = "Authorization"
    prefix: str = Field(default="Bearer ", max_length=32)
    help: str = Field(default="", max_length=300)

    @field_validator("header")
    @classmethod
    def _check_header(cls, value: str) -> str:
        if not HEADER_NAME.match(value) or value.lower() in RESERVED_HEADERS:
            raise ValueError(f"请求头 {value[:64]} 的名字不合法或不允许配置")
        return value

    @field_validator("prefix")
    @classmethod
    def _check_prefix(cls, value: str) -> str:
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
            raise ValueError("前缀含控制字符")
        return value

    def headers(self, token: str) -> dict[str, str]:
        return {self.header: f"{self.prefix}{token}"}


def _plain(value: str | None, limit: int, what: str) -> str | None:
    """client_id、scope 这类：去掉首尾空白，空串当没填；不许控制字符。"""
    if value is None:
        return None
    text = value.strip()
    if len(text) > limit or any(ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise ValueError(f"{what}过长或含控制字符")
    return text or None


class OAuthInput(_Model):
    """OAuth 授权的设置。client_id 不填就动态注册；填了就用管理员在对方那边建好的应用（client_secret 可选，
    修改时同一个 client_id 不填 secret 表示沿用原来的）。scope 不填用对方建议的。"""

    client_id: str | None = None
    client_secret: str | None = None
    scope: str | None = None

    @field_validator("client_id")
    @classmethod
    def _check_client_id(cls, value: str | None) -> str | None:
        return _plain(value, 200, "Client ID ")

    @field_validator("client_secret")
    @classmethod
    def _check_client_secret(cls, value: str | None) -> str | None:
        return _plain(value, 1000, "Client Secret ")

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, value: str | None) -> str | None:
        return _plain(value, 500, "scope ")


class McpServerCreate(_ServerFields):
    name: str = Field(pattern=SERVER_NAME_PATTERN)
    title: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    # 认证方式：shared 共享凭据（用 headers），user_token 个人访问令牌（用 token_config，token 是登记人自己的令牌，
    # 只用来测试连接、同步工具清单，存成他自己的连接），oauth OAuth 授权（用 oauth；登记后登记人登录一次才拉工具清单）
    auth: Literal["shared", "user_token", "oauth"] = "shared"
    token_config: TokenConfig | None = None
    token: str | None = None
    oauth: OAuthInput | None = None

    @field_validator("token")
    @classmethod
    def _check_token(cls, value: str | None) -> str | None:
        return _token(value)


class McpServerPatch(_ServerFields):
    title: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None  # 给出时整体替换；不给表示不改
    timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    enabled: bool | None = None
    auth: Literal["shared", "user_token", "oauth"] | None = None
    token_config: TokenConfig | None = None
    token: str | None = None  # 改成个人访问令牌、或改地址时，用这份（管理员自己的）测试；不给就用他已有的连接
    oauth: OAuthInput | None = None  # 给出时替换 OAuth 授权的设置

    @field_validator("token")
    @classmethod
    def _check_token(cls, value: str | None) -> str | None:
        return _token(value)


class ConnectBody(_Model):
    token: str

    @field_validator("token")
    @classmethod
    def _check_token(cls, value: str) -> str:
        return _token_text(value)


class ProbeBody(_Model):
    url: str

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        checked = _url(value)
        if checked is None:
            raise ValueError("请填写地址")
        return checked


class ProbeView(_Model):
    """登记前探一次：不要凭据就默认无需认证（对方另外支持 OAuth 时 oauth 也是 true，可选）；要凭据时声明了 OAuth
    默认 OAuth 授权，否则默认个人访问令牌。
    不支持自动注册时要填 client。回调地址给管理员填到对方那边。"""

    oauth: bool
    needs_credentials: bool
    dynamic_registration: bool
    scope: str | None
    redirect_uri: str


class LoginBody(_Model):
    """点「连接」跳去登录：return_to 是走完后回到的站内页面。"""

    return_to: str = "/settings?tab=connections"

    @field_validator("return_to")
    @classmethod
    def _check_return_to(cls, value: str) -> str:
        # 只许站内路径：开头一个斜杠、不能是 //host 或 /\host 这类会被浏览器当成别的站点的写法
        if (not value.startswith("/") or value.startswith("//") or "\\" in value or len(value) > 500
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
            raise ValueError("回到的页面必须是站内路径")
        return value


class LoginView(_Model):
    authorize_url: str


class McpToolPatch(_Model):
    """None 不改。policy：允许（allow）、需确认（ask）、禁用（block）；group：只读（read）、写入（write），
    只影响分组与运行记录、审计里的风险标注，不影响三档。改过就不再标「新」。"""

    policy: Literal["allow", "ask", "block"] | None = None
    group: Literal["read", "write"] | None = None


class McpToolView(_Model):
    name: str
    raw_name: str
    description: str
    input_schema: dict[str, Any]
    policy: str  # allow | ask | block
    group: str  # read | write
    annotated_group: str  # 对方注解里的分组（管理员挪过组时能看出来）
    is_new: bool
    problem: str | None

    @classmethod
    def of(cls, tool: CatalogTool) -> "McpToolView":
        return cls(name=tool.name, raw_name=tool.raw_name, description=tool.description,
                   input_schema=tool.input_schema, policy=tool.policy, group=tool.group,
                   annotated_group="read" if tool.annotated_risk == "read" else "write", is_new=tool.is_new,
                   problem=tool.problem)


class HeaderView(_Model):
    name: str
    last4: str


class OAuthView(_Model):
    """OAuth 授权的设置（不含 secret）。dynamic：自动注册的应用；否则 client_id 是管理员填的。"""

    dynamic: bool
    client_id: str
    has_secret: bool
    scope: str | None  # 管理员填的
    suggested_scope: str | None  # 对方建议的
    redirect_uri: str

    @classmethod
    def of(cls, config: dict, redirect_uri: str) -> "OAuthView":
        server = config.get("server") or {}
        return cls(dynamic=bool(config.get("dynamic")), client_id=str(config.get("client_id") or ""),
                   has_secret=bool(config.get("has_secret")), scope=config.get("scope"),
                   suggested_scope=server.get("scope"), redirect_uri=redirect_uri)


class McpServerView(_Model):
    id: str
    name: str
    title: str
    url: str
    headers: list[HeaderView]
    timeout_seconds: int
    enabled: bool
    auth: str
    token_config: TokenConfig | None
    oauth: OAuthView | None
    tools: list[McpToolView]
    grant_pattern: str
    synced_at: datetime | None
    sync_error: str | None
    created_by: str
    updated_at: datetime | None
    audience: list[str] = Field(default_factory=list)  # 企业 MCP 发给哪些空间（"*" 是全部空间）；空间服务为空
    promoted_to: str | None = None  # 空间服务已收为企业 MCP 时，那个企业 MCP 的名称

    @classmethod
    def of(cls, record: McpServerRecord, redirect_uri: str = "", promoted_to: str | None = None) -> "McpServerView":
        config = record.auth_config or {}
        return cls(id=record.id, name=record.name, title=record.title, url=record.url,
                   headers=[HeaderView(**item) for item in record.header_names],
                   timeout_seconds=record.timeout_seconds, enabled=record.enabled, auth=record.auth,
                   token_config=TokenConfig(**config) if record.auth == "user_token" and config else None,
                   oauth=OAuthView.of(config, redirect_uri) if record.auth == "oauth" and config else None,
                   tools=[McpToolView.of(tool) for tool in record.tools],
                   grant_pattern=server_tool_pattern(record.name), synced_at=record.synced_at,
                   sync_error=record.sync_error, created_by=record.created_by, updated_at=record.updated_at,
                   audience=list(record.audience), promoted_to=promoted_to)


class ConnectionTool(_Model):
    name: str  # 对方的原名
    description: str


class ConnectionView(_Model):
    """「设置 → 连接」里的一行：本人能用的服务与工具。status：ready 不用连接（无需认证、共享凭据），
    connected 已连接，invalid 对方拒绝过、要重新连接，none 还没连接。"""

    server_id: str
    title: str
    auth: str
    help: str
    status: Literal["ready", "connected", "invalid", "none"]
    display: str
    connected_at: datetime | None
    tools: list[ConnectionTool]


class EnterpriseKeyBody(_Model):
    """空间管理员在本空间停用、启用某个企业 MCP。"""

    server_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")


class EnterpriseUsersBody(EnterpriseKeyBody):
    """改某个企业 MCP 在本空间给谁用：含全体成员（role:editor）就是不收窄。"""

    subjects: list[Annotated[str, Field(min_length=1, max_length=80)]] = Field(max_length=200)
