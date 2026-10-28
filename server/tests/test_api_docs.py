"""接口文档：/api/docs 页面与 /api/openapi.json 公开，页面的脚本与样式随服务发（不走 CDN）；每个接口都有中文标题、
按中文分组、声明了 API Key 与 Cookie 两种认证，不用认证的接口单独标出。新加的接口忘了写说明，这里会拦住。"""

import secrets

import httpx
import pytest

from knowwork_server.apidocs import PUBLIC, tag_of
from knowwork_server.app import build_runtime, create_public_app
from knowwork_server.config import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
async def http():
    # 只建应用、不连数据库：文档相关的几个接口都不碰库
    runtime = build_runtime(Settings(database_url="postgresql+asyncpg://x@127.0.0.1:1/x",
                                     master_key=secrets.token_hex(32), host_token="h" * 32))
    app = create_public_app(runtime)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as client:
        yield client


async def test_the_docs_page_and_assets_are_public_and_served_locally(http):
    page = await http.get("/api/docs")
    assert page.status_code == 200 and "KnowWork 接口文档" in page.text
    assert "/api/docs/assets/swagger-ui-bundle.js" in page.text and "cdn.jsdelivr" not in page.text
    assert (await http.get("/api/docs/assets/swagger-ui-bundle.js")).status_code == 200
    assert (await http.get("/api/docs/assets/swagger-ui.css")).status_code == 200
    assert (await http.get("/docs")).status_code == 404, "不再用 FastAPI 默认的 /docs（网关只转发 /api）"


async def test_every_operation_has_a_chinese_title_and_group(http):
    schema = (await http.get("/api/openapi.json")).json()
    operations = [(method.upper(), path, op) for path, item in schema["paths"].items() for method, op in item.items()]
    assert len(operations) > 100
    untitled = [f"{m} {p}" for m, p, op in operations if op.get("summary", "").isascii()]
    assert untitled == [], "这些接口没有中文标题：处理函数的说明第一行就是标题"
    ungrouped = [f"{m} {p}" for m, p, op in operations if op["tags"] == ["其他"]]
    assert ungrouped == [], "这些接口没分组：在 apidocs.tag_of 里按路径归一个组"
    assert [t["name"] for t in schema["tags"]][:2] == ["对外知识接口", "对话"]


async def test_auth_schemes_are_declared_and_public_endpoints_marked(http):
    schema = (await http.get("/api/openapi.json")).json()
    assert set(schema["components"]["securitySchemes"]) == {"ApiKey", "Session"}
    assert schema["security"] == [{"ApiKey": []}, {"Session": []}]
    login = schema["paths"]["/api/auth/login"]["post"]
    conversations = schema["paths"]["/api/workspaces/{workspace_id}/conversations"]["get"]
    assert login["security"] == [] and "security" not in conversations
    assert {p for p in schema["paths"] if any(op.get("security") == [] for op in schema["paths"][p].values())} \
        <= PUBLIC


@pytest.mark.parametrize(("path", "tag"), [
    ("/api/v1/knowledge/search", "对外知识接口"),
    ("/api/workspaces/{workspace_id}/conversations/{conversation_id}/messages", "对话"),
    ("/api/workspaces/{workspace_id}/me/skills", "技能"),
    ("/api/workspaces/{workspace_id}/me/check", "成员与权限"),
    ("/api/system/models", "模型"),
    ("/health", "运维"),
    ("/api/workspaces/{workspace_id}/somewhere-new", None),
])
def test_groups_follow_the_path(path, tag):
    assert tag_of(path) == tag
