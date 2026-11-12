"""网页嵌入走普通的对话接口（channels/web.py）：真实的认人与调用者依赖（principals.conversation_actor）、
真实的会话服务（宿主换成假的），拿真的会话令牌来调。

- 访客开的会话记下渠道与来源，会话位置按访客记；两个访客互相碰不到（带会话 id 的接口一处管住，404），列表只列自己的；
- 发消息前限次（429）；
- 管理接口（要别的范围）403，运行详情这类不给嵌入的 403，暂存附件这类按人列的不给访客；
- 不带令牌、令牌作废了一律 401；
- 扩展里嵌入也能用的接口（例如问数卡片上的下载，路径里没有会话 id）经 EmbedGuard 核对：是自己的会话、按访客限次。"""

from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI, Request

from knowwork_server.auth.principal import Principal
from knowwork_server.channels import build_router as channels_router
from knowwork_server.channels import build_web_router
from knowwork_server.conversations.api import Actor
from knowwork_server.conversations.api import build_router as conversation_router
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.models import Conversation
from knowwork_server.conversations.pump import EventPump
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.db.base import in_workspace
from knowwork_server.identity import IdentityConfig, build_identity
from knowwork_server.principals import (
    CONVERSATIONS_SCOPE,
    WORKSPACE_SCOPE,
    EmbedGuard,
    conversation_actor,
    path_workspace,
    scoped_principal,
)

from ..fakes import FakeKernel
from .test_web import ALICE, BOB, WEB

pytestmark = pytest.mark.anyio


@pytest.fixture
async def app(env):
    identity = build_identity(env.global_sessionmaker, IdentityConfig(public_base_url="https://kw.example.com"))
    web = env.channels.web
    identity.embed.set(web.resolve)
    kernel = FakeKernel()
    writer, broadcaster = BatchWriter(env.scoped), Broadcaster()
    pump = EventPump(kernel, env.scoped, writer, broadcaster)
    service = ConversationService(kernel, env.scoped, pump, EventFeed(env.scoped, writer, broadcaster,
                                                                      pump.produced_upto))
    actor = conversation_actor(identity, env.access, web)
    no_embed = conversation_actor(identity, env.access)
    no_visitor = conversation_actor(identity, env.access, web, visitors=False)
    application = FastAPI(dependencies=[Depends(path_workspace)])
    application.include_router(conversation_router(service, actor, embedded=web))
    application.include_router(channels_router(env.channels.admin, scoped_principal(identity, WORKSPACE_SCOPE)))
    application.include_router(build_web_router(web))

    @application.get("/api/workspaces/{workspace_id}/conversations/{conversation_id}/debug/turns")
    async def debug(conversation_id: str, who: Annotated[Actor, Depends(no_embed)]):
        return {"ok": True}

    @application.get("/api/workspaces/{workspace_id}/attachments")
    async def staged(who: Annotated[Actor, Depends(no_visitor)]):
        return {"items": []}

    # 扩展的卡片操作（按查询编号来，这里直接拿会话 id 代替）：嵌入也能用，碰会话前过 EmbedGuard
    guard = EmbedGuard(env.access)
    guard.bind(web)
    unbound = EmbedGuard(env.access)
    card_caller = scoped_principal(identity, CONVERSATIONS_SCOPE)

    @application.post("/api/workspaces/{workspace_id}/probe/{conversation_id}/act")
    async def act(conversation_id: str, request: Request, who: Annotated[Principal, Depends(card_caller)]):
        await guard.check(who, request, conversation_id)
        return {"ok": True}

    @application.post("/api/workspaces/{workspace_id}/probe/{conversation_id}/unbound")
    async def act_unbound(conversation_id: str, request: Request, who: Annotated[Principal, Depends(card_caller)]):
        await unbound.check(who, request, conversation_id)
        return {"ok": True}

    try:
        yield application, service
    finally:
        await pump.close()


@pytest.fixture
async def http(app):
    application, _ = app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://kw") as client:
        yield client


async def web_channel(env) -> dict:
    from knowwork_server.channels.admin import ChannelInput
    from knowwork_server.channels.settings import ChannelSettings, WebOptions
    with in_workspace(env.ws):
        view = await env.channels.admin.create("boss", env.ws, ChannelInput(
            name="官网助手", platform="web", enabled=True, bot_id="", bot_name="", secret=None,
            settings=ChannelSettings(**WEB["settings"]), web=WebOptions(**WEB["web"])))
    return {"id": view.id, "key": view.web["web_key"]}


async def token(http, key: str, visitor: str) -> dict[str, str]:
    opened = await http.post(f"/api/embed/v1/channels/{key}/sessions", json={"visitor": visitor})
    assert opened.status_code == 200, opened.text
    return {"authorization": f"Bearer {opened.json()['token']}"}


async def test_visitors_chat_in_their_own_conversations(env, http):
    channel = await web_channel(env)
    alice, bob = await token(http, channel["key"], ALICE), await token(http, channel["key"], BOB)
    conversations = f"/api/workspaces/{env.ws}/conversations"
    created = await http.post(conversations, json={"title": "问销售额"}, headers=alice)
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    with in_workspace(env.ws):
        async with env.scoped() as session:
            row = await session.get(Conversation, conversation_id)
    assert (row.user_id, row.channel_id, row.source) == (channel["id"], channel["id"], "web")
    # 自己的：看得到、列得出
    assert (await http.get(f"{conversations}/{conversation_id}/scope", headers=alice)).status_code == 200
    listed = (await http.get(conversations, headers=alice)).json()["items"]
    assert [item["id"] for item in listed] == [conversation_id]
    assert (await http.get(f"{conversations}/search", params={"q": "销售"}, headers=alice)).json() == {"items": []}
    # 别的访客：同一个渠道账号，也碰不到
    assert (await http.get(f"{conversations}/{conversation_id}/scope", headers=bob)).status_code == 404
    assert (await http.post(f"{conversations}/{conversation_id}/messages", json={"text": "hi"},
                            headers=bob)).status_code == 404
    assert (await http.get(conversations, headers=bob)).json()["items"] == []


async def test_asking_too_fast_is_429(env, http):
    channel = await web_channel(env)  # 每个访客每分钟 2 次
    alice = await token(http, channel["key"], ALICE)
    conversations = f"/api/workspaces/{env.ws}/conversations"
    conversation_id = (await http.post(conversations, json={}, headers=alice)).json()["id"]
    statuses = [(await http.post(f"{conversations}/{conversation_id}/messages", json={"text": "hi"},
                                 headers=alice)).status_code for _ in range(3)]
    assert statuses[2] == 429 and 429 not in statuses[:2]


async def test_starting_conversations_too_fast_is_429(env, http):
    """每新开一段对话宿主就建一个会话：访客拿着一个令牌连续新开，按提问的次数限（每分钟 2 次）。"""
    channel = await web_channel(env)
    alice = await token(http, channel["key"], ALICE)
    conversations = f"/api/workspaces/{env.ws}/conversations"
    statuses = [(await http.post(conversations, json={}, headers=alice)).status_code for _ in range(3)]
    assert statuses == [201, 201, 429]


async def test_embedded_callers_stay_out_of_everything_else(env, http):
    channel = await web_channel(env)
    alice = await token(http, channel["key"], ALICE)
    conversation_id = (await http.post(f"/api/workspaces/{env.ws}/conversations", json={}, headers=alice)).json()["id"]
    assert (await http.get(f"/api/workspaces/{env.ws}/channels", headers=alice)).status_code == 403
    debug = f"/api/workspaces/{env.ws}/conversations/{conversation_id}/debug/turns"
    assert (await http.get(debug, headers=alice)).status_code == 403
    assert (await http.get(f"/api/workspaces/{env.ws}/attachments", headers=alice)).status_code == 403
    # 别的空间：令牌绑着渠道所在的空间
    assert (await http.get("/api/workspaces/other/conversations", headers=alice)).status_code == 403


async def test_missing_or_revoked_tokens_are_401(env, http):
    channel = await web_channel(env)
    alice = await token(http, channel["key"], ALICE)
    conversations = f"/api/workspaces/{env.ws}/conversations"
    assert (await http.get(conversations)).status_code == 401
    assert (await http.get(conversations, headers={"authorization": "Bearer emb_forged.x"})).status_code == 401
    with in_workspace(env.ws):
        await env.channels.admin.reset_web_key("boss", env.ws, channel["id"])
    assert (await http.get(conversations, headers=alice)).status_code == 401


async def test_extension_card_actions_check_the_conversation_and_throttle(env, http):
    channel = await web_channel(env)  # 每个访客每分钟问 2 次；卡片操作是它的 3 倍
    alice, bob = await token(http, channel["key"], ALICE), await token(http, channel["key"], BOB)
    conversation_id = (await http.post(f"/api/workspaces/{env.ws}/conversations", json={},
                                       headers=alice)).json()["id"]
    probe = f"/api/workspaces/{env.ws}/probe/{conversation_id}"
    # 别的访客：同一个渠道账号，查询编号猜到了也像不存在
    assert (await http.post(f"{probe}/act", headers=bob)).status_code == 404
    statuses = [(await http.post(f"{probe}/act", headers=alice)).status_code for _ in range(7)]
    assert statuses[:6] == [200] * 6 and statuses[6] == 429
    # 没接上网页渠道的（例如测试或没装渠道模块）：嵌入身份一律不给用
    assert (await http.post(f"{probe}/unbound", headers=alice)).status_code == 403


async def test_embed_guard_lets_ordinary_callers_through(env):
    guard = EmbedGuard(env.access)
    await guard.check(Principal(user_id="boss", auth_method="session"), None, "conv_x")


def test_principal_fields_default_to_none():
    principal = Principal(user_id="u", auth_method="session")
    assert (principal.channel_id, principal.visitor) == (None, None)


async def test_identified_people_chat_as_themselves(env, http):
    from .test_web_identified import sign
    with in_workspace(env.ws):
        from knowwork_server.channels.admin import ChannelInput
        from knowwork_server.channels.settings import ChannelSettings, WebOptions
        view = await env.channels.admin.create("boss", env.ws, ChannelInput(
            name="BI 助手", platform="web", enabled=True, bot_id="", bot_name="", secret=None,
            settings=ChannelSettings(answer_mode="agent"), web=WebOptions(**WEB["web"]), audience="member"))
        _, secret = await env.channels.admin.reset_web_secret("boss", env.ws, view.id)
    key = view.web["web_key"]

    async def identified(sub: str) -> dict[str, str]:
        opened = await http.post(f"/api/embed/v1/channels/{key}/sessions", json={"token": sign(secret, sub=sub)})
        assert opened.status_code == 200, opened.text
        return {"authorization": f"Bearer {opened.json()['token']}"}

    zhang, li = await identified("bi_zhang"), await identified("bi_li")
    conversations = f"/api/workspaces/{env.ws}/conversations"
    conversation_id = (await http.post(conversations, json={}, headers=zhang)).json()["id"]
    with in_workspace(env.ws):
        async with env.scoped() as session:
            row = await session.get(Conversation, conversation_id)
    assert row.user_id != view.id and row.channel_id == view.id, "记在本人名下，不是渠道账号"
    assert [item["id"] for item in (await http.get(conversations, headers=zhang)).json()["items"]] == [conversation_id]
    assert (await http.get(f"{conversations}/{conversation_id}/scope", headers=li)).status_code == 404
