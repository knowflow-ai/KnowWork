"""HTTP 接口：厂商与模型的管理接口只给系统管理员（空间管理员也不行）；密钥不回显；连通测试成功与各类失败；成员看自己能用的模型与用量。"""

import json
from datetime import UTC, datetime
from typing import Annotated

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException

from knowwork_server.auth.principal import Principal
from knowwork_server.models.api import build_router
from knowwork_server.models.usage import UsageContext, usage_from_event

pytestmark = pytest.mark.anyio
KEY = "sk-live-api-key-9876ZYXW"
BASE = "/api/workspaces/ws1"
SYS = "/api/system"


async def principal(x_user: Annotated[str | None, Header()] = None) -> Principal:
    if not x_user:
        raise HTTPException(status_code=401, detail="未登录")
    return Principal(user_id=x_user, auth_method="session")


class Provider:
    """厂商桩：按路径记下请求，按 mode 返回。"""

    def __init__(self):
        self.mode, self.requests = "ok", []

    def __call__(self, request: httpx.Request):
        self.requests.append({"path": request.url.path, "auth": request.headers.get("authorization"),
                              "body": json.loads(request.content)})
        if self.mode == "connect":
            raise httpx.ConnectError("refused 10.0.0.4")
        if self.mode == "timeout":
            raise httpx.ReadTimeout("slow")
        if self.mode == "html":
            return httpx.Response(200, text="<html>")
        if self.mode in ("401", "404", "429", "500"):
            return httpx.Response(int(self.mode), json={"error": f"detail with {KEY}"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "p"}}]})


@pytest.fixture
async def setup(models, access):
    provider = Provider()
    app = FastAPI()
    probe_http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    app.include_router(build_router(models.catalog, models.recorder, access, principal, probe_http))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        yield http, provider, models
    await probe_http.aclose()


def as_(user):
    return {"x-user": user}


async def _create(http):
    provider = (await http.post(f"{SYS}/model-providers", headers=as_("root"), json={
        "name": "DeepSeek", "base_url": "https://api.deepseek.example/v1", "api_key": KEY})).json()
    model = (await http.post(f"{SYS}/models", headers=as_("root"), json={
        "provider_id": provider["id"], "alias": "default", "name": "deepseek-chat",
        "input_price_per_mtok": "2"})).json()
    return provider, model


async def test_admin_crud_and_key_never_echoed(setup):
    http, _, _ = setup
    provider, model = await _create(http)
    assert provider["api_key_configured"] and provider["api_key_last4"] == "ZYXW"
    for path in ("model-providers", "models"):
        response = await http.get(f"{SYS}/{path}", headers=as_("root"))
        assert response.status_code == 200 and KEY not in response.text
    patched = await http.patch(f"{SYS}/model-providers/{provider['id']}", headers=as_("root"), json={"name": "DS"})
    assert patched.json()["name"] == "DS" and KEY not in patched.text
    assert (await http.patch(f"{SYS}/models/{model['id']}", headers=as_("root"),
                             json={"enabled": False})).json()["enabled"] is False
    assert (await http.delete(f"{SYS}/model-providers/{provider['id']}", headers=as_("root"))).status_code == 409
    assert (await http.delete(f"{SYS}/models/{model['id']}", headers=as_("root"))).status_code == 204
    assert (await http.delete(f"{SYS}/models/{model['id']}", headers=as_("root"))).status_code == 404
    assert (await http.delete(f"{SYS}/model-providers/{provider['id']}", headers=as_("root"))).status_code == 204


async def test_validation_and_auth(setup):
    http, _, _ = setup
    assert (await http.get(f"{SYS}/models")).status_code == 401
    for user in ("alice", "bob", "carol", "dave", "mallory"):
        assert (await http.get(f"{SYS}/models", headers=as_(user))).status_code == 403
        assert (await http.post(f"{SYS}/model-providers", headers=as_(user),
                                json={"name": "x", "base_url": "https://h/v1"})).status_code == 403
    bad = await http.post(f"{SYS}/model-providers", headers=as_("root"), json={"name": "x", "base_url": "ftp://h"})
    assert bad.status_code == 422
    unknown = await http.post(f"{SYS}/model-providers", headers=as_("root"),
                              json={"name": "x", "base_url": "https://h/v1", "extra": 1})
    assert unknown.status_code == 422
    missing = await http.post(f"{SYS}/models", headers=as_("root"),
                              json={"provider_id": "mp_x", "alias": "a", "name": "n"})
    assert missing.status_code == 404


@pytest.mark.parametrize(("mode", "ok", "status", "message"), [
    ("ok", True, 200, "连通正常"),
    ("401", False, 401, "鉴权失败，请检查 API 密钥"),
    ("404", False, 404, "地址或模型名不存在"),
    ("429", False, 429, "上游限流或额度不足"),
    ("500", False, 500, "上游返回错误（HTTP 500）"),
    ("html", False, 200, "上游返回的不是 JSON，地址可能不是 OpenAI 兼容接口"),
    ("connect", False, None, "无法连接到 API 地址"),
    ("timeout", False, None, "连接超时"),
])
async def test_connectivity_test(setup, mode, ok, status, message):
    http, provider, _ = setup
    _, model = await _create(http)
    provider.mode = mode
    response = await http.post(f"{SYS}/models/{model['id']}/test", headers=as_("root"))
    result = response.json()
    assert response.status_code == 200
    assert (result["ok"], result["upstream_status"], result["message"], result["capability"]) == (ok, status,
                                                                                                  message, "chat")
    assert KEY not in response.text and "10.0.0.4" not in response.text
    sent = provider.requests[-1]
    assert sent["path"] == "/v1/chat/completions" and sent["auth"] == f"Bearer {KEY}"
    assert sent["body"]["model"] == "deepseek-chat" and sent["body"]["max_tokens"] == 1


@pytest.mark.parametrize(("capability", "path"), [("embedding", "/v1/embeddings"), ("rerank", "/v1/rerank")])
async def test_connectivity_test_by_capability(setup, capability, path):
    http, provider, _ = setup
    _, model = await _create(http)
    response = await http.post(f"{SYS}/models/{model['id']}/test", params={"capability": capability},
                               headers=as_("root"))
    assert response.json()["ok"] and provider.requests[-1]["path"] == path
    assert (await http.post(f"{SYS}/models/{model['id']}/test", headers=as_("dave"))).status_code == 403


async def test_member_views(setup):
    """模型不按人授权（2026-10-23 去掉了模型分组）：分组接口与「我能用的模型」接口都没有了；成员看得到自己的用量。"""
    http, _, models = setup
    await _create(http)
    for path in ("model-groups", "usable-models"):
        assert (await http.get(f"{BASE}/{path}", headers=as_("bob"))).status_code == 404
    ctx = UsageContext(workspace_id="ws1", user_id="dave", conversation_id="c1", model_alias="default")
    data = {"turn": 0, "step": 0, "usage": {"inputTokens": 10, "outputTokens": 5}}
    await models.recorder.record(usage_from_event("assistant/message", data, ctx, datetime(2026, 10, 18, 4, tzinfo=UTC)))
    params = {"start": "2026-10-18", "end": "2026-10-18"}
    mine = await http.get(f"{BASE}/usage/me", params=params, headers=as_("dave"))
    assert [(r["key"], r["total_tokens"]) for r in mine.json()] == [("default", 15)]
    assert (await http.get(f"{BASE}/usage/me", params={**params, "group_by": "user"},
                           headers=as_("dave"))).status_code == 422
    assert (await http.get(f"{BASE}/usage/me", params=params, headers=as_("mallory"))).status_code == 403
    assert (await http.get(f"{BASE}/usage", params=params, headers=as_("dave"))).status_code == 403
    everyone = await http.get(f"{BASE}/usage", params={**params, "group_by": "user"}, headers=as_("bob"))
    assert [(r["key"], r["calls"]) for r in everyone.json()] == [("dave", 1)]
    bad_range = await http.get(f"{BASE}/usage", params={"start": "2026-10-19", "end": "2026-10-18"},
                               headers=as_("bob"))
    assert bad_range.status_code == 422
