"""网页嵌入的会话令牌怎么认（identity/embed.py、principals.conversation_actor）：只给声明了范围、而且嵌入身份带齐这些范围的
接口用（目前是对话）；管理接口 403，不查范围的接口（get_principal）一律 401；带着嵌入令牌的请求就算也带着登录 Cookie，
也按嵌入令牌认，不让 Cookie 混进来。令牌的签发与核对在渠道模块，这里登记一个假的解析函数。"""

from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI

from knowwork_server.auth.principal import Principal

from .conftest import BASE_URL, login

pytestmark = pytest.mark.anyio

GOOD = "emb_good.token"
VISITOR = "v-0123456789abcdef"


async def resolve(token: str) -> Principal | None:
    if token != GOOD:
        return None
    return Principal(user_id="ch_1", auth_method="embed", scopes=frozenset({"conversations:use"}), workspace_id="ws1",
                     channel_id="ch_1", visitor=VISITOR)


def embed_app(identity) -> FastAPI:
    app = FastAPI()
    from knowwork_server.identity.api import build_router
    app.include_router(build_router(identity, is_admin=lambda who, workspace_id: False,
                                    can_bind_workspace=lambda who, workspace_id: True))
    Chat = Annotated[Principal, Depends(identity.require_scopes("conversations:use"))]
    Manage = Annotated[Principal, Depends(identity.require_scopes("workspace:manage"))]
    Unscoped = Annotated[Principal, Depends(identity.require_scopes())]
    Anyone = Annotated[Principal, Depends(identity.get_principal)]

    @app.get("/api/probe/chat")
    async def chat(who: Chat):
        return {"userId": who.user_id, "method": who.auth_method, "channel": who.channel_id, "visitor": who.visitor}

    @app.get("/api/probe/manage")
    async def manage(who: Manage):
        return {"userId": who.user_id}

    @app.get("/api/probe/unscoped")
    async def unscoped(who: Unscoped):
        return {"userId": who.user_id}

    @app.get("/api/probe/anyone")
    async def anyone(who: Anyone):
        return {"userId": who.user_id}

    return app


@pytest.fixture
async def http(identity):
    identity.embed.set(resolve)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=embed_app(identity)), base_url=BASE_URL) as client:
        yield client


def bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_embed_tokens_reach_conversation_routes_only(http):
    chat = await http.get("/api/probe/chat", headers=bearer(GOOD))
    assert chat.status_code == 200
    assert chat.json() == {"userId": "ch_1", "method": "embed", "channel": "ch_1", "visitor": VISITOR}
    assert (await http.get("/api/probe/manage", headers=bearer(GOOD))).status_code == 403
    assert (await http.get("/api/probe/unscoped", headers=bearer(GOOD))).status_code == 403
    assert (await http.get("/api/probe/anyone", headers=bearer(GOOD))).status_code == 401
    assert (await http.get("/api/auth/me", headers=bearer(GOOD))).status_code == 401


async def test_unknown_embed_tokens_are_unauthorized(http):
    response = await http.get("/api/probe/chat", headers=bearer("emb_forged.token"))
    assert response.status_code == 401 and response.json()["detail"] == "未登录或凭证无效"


async def test_an_embed_token_wins_over_a_login_cookie(http, alice):
    assert (await login(http)).status_code == 200
    # 只带 Cookie：照常是登录的人
    assert (await http.get("/api/probe/anyone")).json() == {"userId": alice.id}
    # 同时带嵌入令牌：对话接口按嵌入身份，不查范围的接口不认（不退回 Cookie）
    assert (await http.get("/api/probe/chat", headers=bearer(GOOD))).json()["userId"] == "ch_1"
    assert (await http.get("/api/probe/anyone", headers=bearer(GOOD))).status_code == 401


async def test_without_a_registered_resolver_embed_tokens_are_unauthorized(identity):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=embed_app(identity)), base_url=BASE_URL) as client:
        assert (await client.get("/api/probe/chat", headers=bearer(GOOD))).status_code == 401


def test_embedded_principals_say_so():
    embedded = Principal(user_id="u", auth_method="embed", scopes=frozenset({"conversations:use"}), channel_id="ch_1")
    assert embedded.is_embedded and not embedded.is_api_key
    assert not Principal(user_id="u", auth_method="session").is_embedded
