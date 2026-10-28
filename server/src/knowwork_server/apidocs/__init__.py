"""接口文档（Swagger）：/api/docs 是页面，/api/openapi.json 是 OpenAPI 描述，都在 /api 下，网关与本机前端端口都转得到。
公开，不用登录：接口清单本来就在开源代码里，真正调用仍要登录或带 API Key。

- 页面的脚本与样式随服务一起发（static/，swagger-ui-dist 5，Apache-2.0），不走 CDN：私有化部署常常不通外网；
- 每个接口的标题取处理函数说明的第一行，其余行是详细说明；
- 按路径分成中文的组（TAGS、tag_of），扩展的接口用它自己路由上的组名；
- 声明两种认证：API Key（Authorization: Bearer ak_…）与浏览器登录的 Cookie，页面上「Authorize」填了 Key 就能试；
  登录、初始化、品牌、健康检查这些不用认证的单独标出。
"""

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

DOCS_PATH = "/api/docs"
OPENAPI_PATH = "/api/openapi.json"
STATIC = Path(__file__).with_name("static")
WORKSPACE = "/api/workspaces/{workspace_id}/"

DESCRIPTION = """KnowWork 的全部接口。

- **认证**：浏览器登录后带 Cookie（`kw_session`）；程序调用用 API Key，放在请求头 `Authorization: Bearer ak_…`。
  API Key 在「个人设置 → API Key」新建，建的时候选权限范围，只能调范围内的接口。
- **工作空间**：路径里的 `{workspace_id}` 是空间编号，只有一个空间时是 `default`。
- **错误**：统一返回 `{"detail": "说明"}`，说明面向用户，不含内部细节。
- **对外知识接口**：`/api/v1/knowledge*`，权限范围 `knowledge:read`；同样的能力也以 MCP 提供（`/api/v1/mcp`，流式 HTTP）。
"""

# 组名与说明，按这个顺序排
TAGS: tuple[tuple[str, str], ...] = (
    ("对外知识接口", "给别的系统检索、阅读知识库：权限范围 knowledge:read"),
    ("对话", "对话、发消息、事件流、附件、产物、引用、审批与提问、运行详情"),
    ("知识源", "接入的知识源（KnowFlow 等）与可授权的知识库"),
    ("技能", "技能的新建、草稿、发布、启停与调用记录"),
    ("工作流", "运行工作流、审批步骤、事件流"),
    ("工具（MCP）", "接入的 MCP 服务与它们的工具"),
    ("IM 渠道", "企业微信等机器人接入"),
    ("成员与权限", "成员、部门、用户组、邀请、授权"),
    ("模型", "模型厂商、模型与文档解析服务（系统管理员）"),
    ("空间管理", "多个工作空间（系统管理员）"),
    ("系统设置", "品牌、系统信息"),
    ("用量", "用量统计"),
    ("问答大盘", "问答数据汇总"),
    ("运行记录", "管理员看各会话的运行详情"),
    ("后台任务", "任务队列的运行面板（系统管理员）"),
    ("审计", "审计日志"),
    ("账号与登录", "登录、单点登录、邀请、API Key"),
    ("初始化", "首次部署时创建第一个管理员"),
    ("运维", "健康检查"),
)

_SEGMENTS = {
    "conversations": "对话", "runs": "运行记录", "usage": "用量", "insights": "问答大盘", "channels": "IM 渠道",
    "knowledge-sources": "知识源", "knowledge-bases": "知识源", "mcp-servers": "工具（MCP）", "skills": "技能",
    "workflow-runs": "工作流", "jobs": "后台任务", "audit-logs": "审计", "members": "成员与权限",
    "departments": "成员与权限", "groups": "成员与权限", "grants": "成员与权限", "invitations": "成员与权限",
    "users": "成员与权限",
}

# 不用认证就能调的
PUBLIC = frozenset({
    "/api/auth/login", "/api/auth/oidc/login", "/api/auth/oidc/callback", "/api/auth/invitations/accept",
    "/api/setup", "/api/brand", "/api/brand/logo", "/health", "/ready",
})


def tag_of(path: str) -> str | None:
    """按路径定组；认不出的（扩展的接口）返回 None，用路由自己的组名。"""
    if path in ("/health", "/ready"):
        return "运维"
    if path.startswith("/api/v1/"):
        return "对外知识接口"
    if path.startswith("/api/auth/"):
        return "账号与登录"
    if path.startswith("/api/setup"):
        return "初始化"
    if path.startswith(("/api/brand", "/api/system/brand")) or path in ("/api/system/check", "/api/system/info"):
        return "系统设置"
    if path == "/api/workspaces" or path.startswith("/api/system/workspaces"):
        return "空间管理"
    if path.startswith(("/api/system/models", "/api/system/model-providers", "/api/system/document-parser")):
        return "模型"
    if path.startswith(WORKSPACE):
        rest = path[len(WORKSPACE):].split("/")
        if rest[0] == "me":
            return "技能" if len(rest) > 1 and rest[1] == "skills" else "成员与权限"
        return _SEGMENTS.get(rest[0])
    return None


def _operation(path: str, operation: dict[str, Any]) -> None:
    doc = (operation.get("description") or "").strip()
    if doc:
        first, _, rest = doc.partition("\n")
        operation["summary"] = first.strip()
        if rest.strip():
            operation["description"] = rest.strip()
        else:
            operation.pop("description", None)
    tag = tag_of(path)
    if tag is not None:
        operation["tags"] = [tag]
    elif not operation.get("tags"):
        operation["tags"] = ["其他"]
    if path in PUBLIC:
        operation["security"] = []


def build_schema(app: FastAPI) -> dict[str, Any]:
    schema = get_openapi(title="KnowWork 接口文档", version=app.version, description=DESCRIPTION, routes=app.routes)
    for path, item in schema.get("paths", {}).items():
        for method, operation in item.items():
            if method in ("get", "post", "put", "patch", "delete"):
                _operation(path, operation)
    components = schema.setdefault("components", {})
    components["securitySchemes"] = {
        "ApiKey": {"type": "http", "scheme": "bearer", "description": "API Key：Authorization: Bearer ak_…"},
        "Session": {"type": "apiKey", "in": "cookie", "name": "kw_session", "description": "浏览器登录后的 Cookie"},
    }
    schema["security"] = [{"ApiKey": []}, {"Session": []}]
    # 用到的组按第一次出现的先后（扩展的组跟着它挂路由的顺序）；有说明的按 TAGS 排在前面
    used = list(dict.fromkeys(tag for item in schema.get("paths", {}).values() for op in item.values()
                              if isinstance(op, dict) for tag in op.get("tags", ())))
    known = dict(TAGS)
    schema["tags"] = ([{"name": name, "description": text} for name, text in TAGS if name in used]
                      + [{"name": name} for name in used if name not in known])
    return schema


def install(app: FastAPI) -> None:
    """挂上接口文档。要在全部路由都挂好之后调：OpenAPI 描述第一次被请求时才生成，之后缓存。"""
    app.mount(f"{DOCS_PATH}/assets", StaticFiles(directory=STATIC), name="api-docs-assets")
    cache: dict[str, Any] = {}

    def openapi() -> dict[str, Any]:
        if "schema" not in cache:
            cache["schema"] = build_schema(app)
        return cache["schema"]

    app.openapi = openapi  # type: ignore[method-assign]

    @app.get(OPENAPI_PATH, include_in_schema=False)
    async def spec() -> JSONResponse:
        return JSONResponse(openapi())

    @app.get(DOCS_PATH, include_in_schema=False)
    async def docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=OPENAPI_PATH, title="KnowWork 接口文档",
            swagger_js_url=f"{DOCS_PATH}/assets/swagger-ui-bundle.js",
            swagger_css_url=f"{DOCS_PATH}/assets/swagger-ui.css", swagger_favicon_url="/favicon.ico",
            swagger_ui_parameters={"docExpansion": "none", "persistAuthorization": True, "filter": True,
                                   "displayRequestDuration": True, "tryItOutEnabled": False})
