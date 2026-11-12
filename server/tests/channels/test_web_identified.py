"""网页嵌入的认证访问（channels/web_identity.py）：接入方后端用渠道的认证密钥签 JWT，嵌入页拿它换会话令牌。

- 管理：认证访问的网页渠道不用选资料；认证密钥明文只在生成那一次返回，库里加密存；组对照里的用户组要在空间里；
- 校验：只认 HS256、签名对；sub、exp、jti 必填；有效期不超过 10 分钟；同一个令牌只能用一次；
- 对人：按邮箱对上空间里的人（之后按 sub 直接认）；对不上时自动建外部用户（没有邮箱密码，进空间当成员），按组对照
  同步用户组；关了自动建就不让进；管理员移出空间的不会被加回去；
- 会话令牌：重置认证密钥或停用账号后作废。"""

import secrets
import time
import uuid

import pytest
from joserfc import jwt
from joserfc.jwk import OctKey

from knowwork_server.channels.web_identity import Rejected, verify
from knowwork_server.db.base import in_workspace
from knowwork_server.identity.models import USER_EXTERNAL

from .test_web import BOSS, WEB, http  # noqa: F401  （夹具）

pytestmark = pytest.mark.anyio

MEMBER = {**WEB, "audience": "member", "settings": {"answer_mode": "agent", "bases": []}}


def sign(secret: str, *, sub: str = "bi_1", lifetime: int = 300, alg: str = "HS256", **claims) -> str:
    now = int(time.time())
    payload = {"sub": sub, "iat": now, "exp": now + lifetime, "jti": uuid.uuid4().hex, **claims}
    return jwt.encode({"alg": alg}, payload, OctKey.import_key(secret))


async def member_channel(env, http, **settings) -> tuple[dict, str]:  # noqa: F811
    body = {**MEMBER, "settings": {**MEMBER["settings"], **settings}}
    created = await http.post(f"/api/workspaces/{env.ws}/channels", json=body, headers=BOSS)
    assert created.status_code == 201, created.text
    channel = created.json()
    issued = (await http.post(f"/api/workspaces/{env.ws}/channels/{channel['id']}/web-secret", headers=BOSS)).json()
    return issued["channel"], issued["secret"]


async def open_with(http, key: str, token: str):  # noqa: F811
    return await http.post(f"/api/embed/v1/channels/{key}/sessions", json={"token": token})


def test_tokens_are_checked_strictly():
    secret = secrets.token_urlsafe(32)
    claims = verify(sign(secret, name=" 张三 ", email="ZS@Corp.COM", groups=["华东", "", "华东", 3]), secret)
    assert (claims.subject, claims.name, claims.email, claims.groups) == ("bi_1", "张三", "zs@corp.com", ("华东",))
    for bad, message in [
        (sign("x" * 43), "无效"),  # 别的密钥签的
        (sign(secret, lifetime=-120), "无效"),  # 过期了
        (sign(secret, lifetime=3600), "有效期太长"),
        (sign(secret, sub=""), "无效"),
        (jwt.encode({"alg": "HS256"}, {"sub": "bi_1", "exp": int(time.time()) + 60}, OctKey.import_key(secret)), "无效"),
        ("not.a.jwt", "无效"),
    ]:
        with pytest.raises(Rejected, match=message):
            verify(bad, secret)
    with pytest.raises(Rejected, match="认证密钥"):
        verify(sign(secret), "")


async def test_admin_sets_up_identified_channels(env, http):  # noqa: F811
    channel, secret = await member_channel(env, http)
    assert channel["audience"] == "member" and channel["secret_set"] and channel["secret_mask"] == secret[-4:]
    assert len(secret) >= 40 and secret not in str(channel)
    again = (await http.post(f"/api/workspaces/{env.ws}/channels/{channel['id']}/web-secret", headers=BOSS)).json()
    assert again["secret"] != secret
    # 组对照里的用户组要在空间里
    body = {**MEMBER, "settings": {**MEMBER["settings"], "group_map": {"华东区域经理": "grp_missing"}}}
    assert (await http.post(f"/api/workspaces/{env.ws}/channels", json=body, headers=BOSS)).status_code == 422


async def test_people_are_matched_by_email_then_by_subject(env, http):  # noqa: F811
    person = await env.users.create_user(email="zhang@corp.com", name="张三")
    await env.access.add_member("boss", env.ws, person.id, "editor")
    channel, secret = await member_channel(env, http)
    key = channel["web"]["web_key"]
    opened = await open_with(http, key, sign(secret, email="Zhang@Corp.com"))
    assert opened.status_code == 200, opened.text
    principal = await env.channels.web.resolve(opened.json()["token"])
    assert (principal.user_id, principal.visitor, principal.channel_id) == (person.id, None, channel["id"])
    # 对上以后按 sub 直接认，不用再带邮箱
    again = await open_with(http, key, sign(secret))
    assert (await env.channels.web.resolve(again.json()["token"])).user_id == person.id


async def test_unknown_people_become_external_users_with_mapped_groups(env, http):  # noqa: F811
    group = await env.access.create_group("boss", env.ws, "华东销售")
    channel, secret = await member_channel(env, http, group_map={"华东区域经理": group})
    key = channel["web"]["web_key"]
    opened = await open_with(http, key, sign(secret, sub="bi_9", name="李四", groups=["华东区域经理", "别的组"]))
    assert opened.status_code == 200, opened.text
    user_id = (await env.channels.web.resolve(opened.json()["token"])).user_id
    profile = await env.users.get(user_id)
    assert (profile.kind, profile.name, profile.email) == (USER_EXTERNAL, "李四", None)
    assert env.access.check(user_id, env.ws, "workspace", "use")
    members = {g.id: g.members for g in await env.access.list_groups("boss", env.ws)}
    assert user_id in members[group]
    # 调岗：组没了就移出来，名字跟着接入方改
    await open_with(http, key, sign(secret, sub="bi_9", name="李四（华南）", groups=[]))
    members = {g.id: g.members for g in await env.access.list_groups("boss", env.ws)}
    assert user_id not in members[group]
    assert (await env.users.get(user_id)).name == "李四（华南）"


async def test_two_pages_opened_at_once_make_one_external_user(env, http):  # noqa: F811
    """同一个人同时打开两个页面：两边都还没对上，只建一个外部用户，两边落到同一个人。"""
    import asyncio

    from sqlalchemy import func, select

    from knowwork_server.identity.models import User

    channel, secret = await member_channel(env, http)
    key = channel["web"]["web_key"]
    opened = await asyncio.gather(*(open_with(http, key, sign(secret, sub="bi_twin", name="并发同学")) for _ in range(3)))
    assert [response.status_code for response in opened] == [200, 200, 200]
    people = {(await env.channels.web.resolve(response.json()["token"])).user_id for response in opened}
    async with env.global_sessionmaker() as session:
        created = await session.scalar(select(func.count()).select_from(User).where(User.name == "并发同学"))
    assert len(people) == 1 and created == 1


async def test_who_is_turned_away(env, http):  # noqa: F811
    channel, secret = await member_channel(env, http, auto_create=False)
    key = channel["web"]["web_key"]
    refused = await open_with(http, key, sign(secret, sub="nobody"))
    assert refused.status_code == 403 and "联系管理员开通" in refused.json()["detail"]
    # 同一个令牌只能用一次
    person = await env.users.create_user(email="wang@corp.com", name="王五")
    await env.access.add_member("boss", env.ws, person.id, "editor")
    token = sign(secret, sub="bi_wang", email="wang@corp.com")
    assert (await open_with(http, key, token)).status_code == 200
    replayed = await open_with(http, key, token)
    assert replayed.status_code == 403 and "用过" in replayed.json()["detail"]
    # 别的密钥签的
    assert (await open_with(http, key, sign("y" * 43))).status_code == 403
    # 公开访问的入口不认令牌，认证访问的入口不认访客
    public = (await http.post(f"/api/workspaces/{env.ws}/channels", json=WEB, headers=BOSS)).json()
    assert (await open_with(http, public["web"]["web_key"], sign(secret))).status_code == 404
    visitor = await http.post(f"/api/embed/v1/channels/{key}/sessions", json={"visitor": "v-0123456789abcdef"})
    assert visitor.status_code == 404


async def test_removed_external_users_are_not_added_back(env, http):  # noqa: F811
    channel, secret = await member_channel(env, http)
    key = channel["web"]["web_key"]
    opened = await open_with(http, key, sign(secret, sub="bi_x", name="赵六"))
    user_id = (await env.channels.web.resolve(opened.json()["token"])).user_id
    await env.access.remove_member("boss", env.ws, user_id)
    refused = await open_with(http, key, sign(secret, sub="bi_x"))
    assert refused.status_code == 403 and "不是这个空间的成员" in refused.json()["detail"]


async def test_sessions_die_with_the_secret_or_the_account(env, http):  # noqa: F811
    channel, secret = await member_channel(env, http)
    key = channel["web"]["web_key"]
    token = (await open_with(http, key, sign(secret, sub="bi_y", name="钱七"))).json()["token"]
    principal = await env.channels.web.resolve(token)
    assert principal is not None
    await env.users.set_disabled(principal.user_id, True)
    assert await env.channels.web.resolve(token) is None
    await env.users.set_disabled(principal.user_id, False)
    assert await env.channels.web.resolve(token) is not None
    await http.post(f"/api/workspaces/{env.ws}/channels/{channel['id']}/web-secret", headers=BOSS)
    assert await env.channels.web.resolve(token) is None


async def test_identified_conversations_are_scoped_only_when_resources_are_chosen(env, http):  # noqa: F811
    channel, _ = await member_channel(env, http)
    with in_workspace(env.ws):
        conversation = await env.conversations.create(workspace_id=env.ws, user_id="someone",
                                                      channel_id=channel["id"], source="web")
    scope = await env.channels.web.scope(env.ws, conversation.id)
    assert (scope.answer_mode, scope.knowledge_bases, scope.resources) == ("agent", None, None)
    narrowed, _ = await member_channel(env, http, bases=["builtin.kb1"])
    with in_workspace(env.ws):
        other = await env.conversations.create(workspace_id=env.ws, user_id="someone",
                                               channel_id=narrowed["id"], source="web")
    assert (await env.channels.web.scope(env.ws, other.id)).knowledge_bases == ("builtin.kb1",)
