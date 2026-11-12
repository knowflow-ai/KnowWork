"""网页渠道（channels/web.py）：

- 管理：网页渠道没有机器人与 Secret；建的时候生成带着空间的发布令牌，改配置时沿用，可以单独重置；
  渠道账号照样只拿到选的知识库；
- 公开接口：不登录读得到配置（停用、删了、令牌对不上都是 404）；访客 id 换会话令牌；续期；网关问允许嵌入的网站；
- 会话令牌：渠道停用或发布令牌重置后已经发出去的作废；换令牌按 IP 限次；
- 会话：访客只碰得到自己开的会话，列表只列自己的；发消息前按访客每分钟、按渠道每天限次；
- 对话范围：网页渠道开的会话固定用渠道选的回答方式与知识库，别的会话不管。"""

import httpx
import pytest
from fastapi import FastAPI, Header

from knowwork_server.auth.principal import Principal
from knowwork_server.channels import build_router, build_web_router
from knowwork_server.conversations.api import Actor, Throttled
from knowwork_server.db.base import enter_workspace, in_workspace

pytestmark = pytest.mark.anyio

ALICE = "v-alice-0123456789"
BOB = "v-bob-0123456789ab"
WEB = {"name": "官网助手", "platform": "web", "settings": {"answer_mode": "agent", "bases": ["builtin.kb1"]},
       "web": {"origins": ["https://bi.example.com", "https://*.example.cn"], "title": "AI 问数", "greeting": "你好",
               "starters": ["上月销售额"], "per_minute": 2, "per_day": 3}}


@pytest.fixture
async def http(env):
    async def principal(workspace_id: str | None = None, x_user: str = Header()) -> Principal:
        if workspace_id:
            enter_workspace(workspace_id)
        return Principal(user_id=x_user, auth_method="session")

    app = FastAPI()
    app.include_router(build_router(env.channels.admin, principal))
    app.include_router(build_web_router(env.channels.web))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as client:
        yield client


BOSS = {"x-user": "boss"}


async def make_web(env, http, **changes) -> dict:
    created = await http.post(f"/api/workspaces/{env.ws}/channels", json={**WEB, **changes}, headers=BOSS)
    assert created.status_code == 201, created.text
    return created.json()


async def open_session(http, key: str, visitor: str = ALICE) -> httpx.Response:
    return await http.post(f"/api/embed/v1/channels/{key}/sessions", json={"visitor": visitor})


async def test_web_channels_need_no_bot_and_keep_their_publish_key(env, http):
    root = f"/api/workspaces/{env.ws}/channels"
    channel = await make_web(env, http)
    key = channel["web"]["web_key"]
    assert key.startswith(f"{env.ws}~") and channel["secret_set"] is False
    assert channel["web"]["origins"] == ["https://bi.example.com", "https://*.example.cn"]
    assert (await env.users.get(channel["id"])).name == "官网助手（网页嵌入）"
    grants = await env.access.list_grants("boss", env.ws, subject=f"user:{channel['id']}")
    assert sorted((g.resource, g.action) for g in grants) == [("knowledge_base:builtin.kb1", "use")]
    # 改配置：发布令牌不变
    changed = (await http.put(f"{root}/{channel['id']}", json={**WEB, "web": {**WEB["web"], "title": "数据助手"}},
                              headers=BOSS)).json()
    assert (changed["web"]["web_key"], changed["web"]["title"]) == (key, "数据助手")
    # 重置：换一个
    reset = (await http.post(f"{root}/{channel['id']}/web-key", headers=BOSS)).json()
    assert reset["web"]["web_key"] != key and reset["web"]["web_key"].startswith(f"{env.ws}~")
    assert (await http.post(f"{root}/{channel['id']}/web-key", headers={"x-user": "alice"})).status_code == 403
    # IM 渠道照旧要机器人
    im = {**WEB, "platform": "wecom", "web": None, "secret": "sk-wecom-secret-123456"}
    assert (await http.post(root, json=im, headers=BOSS)).status_code == 422
    # 接入方式不能改
    assert (await http.put(f"{root}/{channel['id']}", json={**im, "bot_id": "b1"}, headers=BOSS)).status_code == 422


async def test_public_config_and_frame_ancestors(env, http):
    channel = await make_web(env, http)
    key = channel["web"]["web_key"]
    response = await http.get(f"/api/embed/v1/channels/{key}")
    assert response.headers["access-control-allow-origin"] == "*", "嵌入脚本在接入方页面里跨站读"
    config = response.json()
    assert config == {"title": "AI 问数", "greeting": "你好", "starters": ["上月销售额"], "color": "", "position": "right",
                      "answerMode": "agent", "access": "public"}
    frames = await http.get("/api/embed/v1/frame-ancestors", params={"key": key})
    assert frames.status_code == 204
    assert frames.headers["x-frame-ancestors"] == "'self' https://bi.example.com https://*.example.cn"
    unknown = await http.get("/api/embed/v1/frame-ancestors", params={"key": "nope"})
    assert unknown.headers["x-frame-ancestors"] == "'self'"
    # 网关的 auth_request 子请求只带原始路径（X-Original-URI）
    via_gateway = await http.get("/api/embed/v1/frame-ancestors", headers={"x-original-uri": f"/embed/{key}?v=1"})
    assert via_gateway.headers["x-frame-ancestors"] == "'self' https://bi.example.com https://*.example.cn"
    other_page = await http.get("/api/embed/v1/frame-ancestors", headers={"x-original-uri": "/chat/abc"})
    assert other_page.headers["x-frame-ancestors"] == "'self'"
    assert (await http.get(f"/api/embed/v1/channels/{env.ws}~{'x' * 24}")).status_code == 404
    assert (await http.get("/api/embed/v1/channels/not-a-key")).status_code == 422
    # 停用：配置读不到，也嵌不进去
    await http.post(f"/api/workspaces/{env.ws}/channels/{channel['id']}/enabled", json={"enabled": False},
                    headers=BOSS)
    assert (await http.get(f"/api/embed/v1/channels/{key}")).status_code == 404
    frames = await http.get("/api/embed/v1/frame-ancestors", params={"key": key})
    assert frames.headers["x-frame-ancestors"] == "'self'"


async def test_visitors_get_tokens_that_die_with_the_channel(env, http):
    channel = await make_web(env, http)
    key = channel["web"]["web_key"]
    opened = await open_session(http, key)
    assert opened.status_code == 200
    body = opened.json()
    assert (body["workspaceId"], body["channelId"]) == (env.ws, channel["id"]) and body["token"].startswith("emb_")
    principal = await env.channels.web.resolve(body["token"])
    assert (principal.user_id, principal.channel_id, principal.visitor, principal.workspace_id) == (
        channel["id"], channel["id"], ALICE, env.ws)
    assert principal.auth_method == "embed" and principal.scopes == frozenset({"conversations:use"})
    renewed = await http.post("/api/embed/v1/sessions/renew", headers={"authorization": f"Bearer {body['token']}"})
    assert renewed.status_code == 200 and renewed.json()["token"] != body["token"]
    assert (await http.post("/api/embed/v1/sessions/renew", headers={"authorization": "Bearer ak_x"})).status_code == 401
    assert (await open_session(http, key, visitor="bad id")).status_code == 422
    # 重置发布令牌：已经发出去的作废，旧的发布令牌也换不到新的
    await http.post(f"/api/workspaces/{env.ws}/channels/{channel['id']}/web-key", headers=BOSS)
    assert await env.channels.web.resolve(body["token"]) is None
    assert (await http.post("/api/embed/v1/sessions/renew",
                            headers={"authorization": f"Bearer {renewed.json()['token']}"})).status_code == 401
    assert (await open_session(http, key)).status_code == 404


async def test_disabled_channels_revoke_tokens(env, http):
    channel = await make_web(env, http)
    token = (await open_session(http, channel["web"]["web_key"])).json()["token"]
    await http.post(f"/api/workspaces/{env.ws}/channels/{channel['id']}/enabled", json={"enabled": False},
                    headers=BOSS)
    assert await env.channels.web.resolve(token) is None


async def test_opening_sessions_is_rate_limited_per_ip(env, http):
    channel = await make_web(env, http)
    key = channel["web"]["web_key"]
    statuses = [(await open_session(http, key)).status_code for _ in range(31)]
    assert statuses[:30] == [200] * 30 and statuses[30] == 429
    # 部署时业务服务在网关后面，按网关带来的真实地址（X-Real-IP）分开计
    other = await http.post(f"/api/embed/v1/channels/{key}/sessions", json={"visitor": ALICE},
                            headers={"x-real-ip": "203.0.113.9"})
    assert other.status_code == 200


def actor(env, channel_id: str, visitor: str | None) -> Actor:
    return Actor(user_id=channel_id, workspace_id=env.ws, channel_id=channel_id, visitor=visitor)


async def started(env, who: Actor) -> str:
    with in_workspace(env.ws):
        conversation = await env.conversations.create(workspace_id=env.ws, user_id=who.user_id,
                                                      channel_id=who.channel_id, source="web")
        await env.channels.web.started(who, conversation.id)
    return conversation.id


async def test_visitors_only_reach_their_own_conversations(env, http):
    channel = await make_web(env, http)
    web = env.channels.web
    alice, bob = actor(env, channel["id"], ALICE), actor(env, channel["id"], BOB)
    first = await started(env, alice)
    assert await web.owns(alice, first) and not await web.owns(bob, first)
    assert await web.mine(alice) == (first,) and await web.mine(bob) == ()
    # 开新对话：会话位置换成新的，旧的就碰不到了
    second = await started(env, alice)
    assert await web.mine(alice) == (second,) and not await web.owns(alice, first)
    sessions = (await http.get(f"/api/workspaces/{env.ws}/channels/{channel['id']}/sessions", headers=BOSS)).json()
    assert [s["display_name"] for s in sessions["items"]] == ["访客 v-alic"]


async def test_asking_is_rate_limited(env, http):
    channel = await make_web(env, http)  # 每个访客每分钟 2 次，整个渠道每天 3 次
    web = env.channels.web
    alice, bob = actor(env, channel["id"], ALICE), actor(env, channel["id"], BOB)
    await web.admit(alice, "1.1.1.1")
    await web.admit(alice, "1.1.1.1")
    with pytest.raises(Throttled, match="问得太快"):
        await web.admit(alice, "1.1.1.1")
    await web.admit(bob, "2.2.2.2")
    with pytest.raises(Throttled, match="今天的提问次数"):
        await web.admit(bob, "2.2.2.2")


async def test_web_conversations_follow_the_channel_settings(env, http):
    channel = await make_web(env, http)
    conversation = await started(env, actor(env, channel["id"], ALICE))
    scope = await env.channels.web.scope(env.ws, conversation)
    assert (scope.label, scope.answer_mode, scope.knowledge_bases) == ("AI 问数", "agent", ("builtin.kb1",))
    # 查数据源的回答方式：不给知识库，数据源写进扩展资源
    numbers = await make_web(env, http, settings={"answer_mode": "numbers", "resources": ["ds1"]})
    other = await started(env, actor(env, numbers["id"], BOB))
    scope = await env.channels.web.scope(env.ws, other)
    assert (scope.answer_mode, scope.knowledge_bases, scope.resources) == ("numbers", (), ("data_source:ds1",))
    # 不是从网页渠道来的会话不管
    with in_workspace(env.ws):
        plain = await env.conversations.create(workspace_id=env.ws, user_id="alice")
    assert await env.channels.web.scope(env.ws, plain.id) is None
