"""渠道的管理接口：只有所有者和管理员能管；Secret 不回明文、修改时不填就保留；至少选一个知识库；同一个机器人不能
配两个渠道；启停、删除会让运行管理重连。渠道账号：建渠道时以成员身份进空间，选的知识库正好同步成它的使用授权，
改名跟着改；删渠道时移出空间、账号禁用。能看谁在问。"""

import httpx
import pytest
from fastapi import FastAPI, Header

from knowwork_server.auth.principal import Principal
from knowwork_server.channels import build_router
from knowwork_server.db.base import enter_workspace, in_workspace

from .conftest import FakeConnection, answer
from .test_service import message

pytestmark = pytest.mark.anyio

BODY = {"name": "行政助手", "platform": "wecom", "bot_id": "bot-1", "bot_name": "行政助手",
        "secret": "sk-wecom-secret-123456", "settings": {"answer_mode": "agent", "bases": ["builtin.kb1"]}}


@pytest.fixture
async def http(env):
    async def principal(workspace_id: str | None = None, x_user: str = Header()) -> Principal:
        if workspace_id:
            enter_workspace(workspace_id)  # 主线里由 app 的全局依赖按路径声明
        return Principal(user_id=x_user, auth_method="session")

    app = FastAPI()
    app.include_router(build_router(env.channels.admin, principal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as client:
        yield client


def as_(user):
    return {"x-user": user}


async def knowledge_grants(env, account):
    grants = await env.access.list_grants("boss", env.ws, subject=f"user:{account}")
    return sorted((g.resource, g.action) for g in grants)


async def test_admins_manage_channels_and_secrets_never_come_back(env, http):
    root = f"/api/workspaces/{env.ws}/channels"
    assert (await http.post(root, json=BODY, headers=as_("alice"))).status_code == 403
    assert (await http.post(root, json={**BODY, "secret": ""}, headers=as_("boss"))).status_code == 422
    no_bases = {**BODY, "settings": {"answer_mode": "agent", "bases": []}}
    assert (await http.post(root, json=no_bases, headers=as_("boss"))).status_code == 422
    created = (await http.post(root, json=BODY, headers=as_("boss"))).json()
    assert (created["secret_mask"], created["secret_set"], created["status"]) == ("3456", True, "connected")
    assert "sk-wecom" not in str(created)
    assert env.platform.started == [(created["id"], "sk-wecom-secret-123456")]
    # 同一个机器人不能再配一个
    assert (await http.post(root, json={**BODY, "name": "另一个"}, headers=as_("boss"))).status_code == 422
    # 改名不填 Secret：保留原来的，并按新配置重连
    changed = (await http.put(f"{root}/{created['id']}", json={**BODY, "name": "行政小助手", "secret": None},
                              headers=as_("boss"))).json()
    assert (changed["name"], changed["secret_mask"]) == ("行政小助手", "3456")
    assert env.platform.stopped == [created["id"]]
    assert env.platform.started[-1] == (created["id"], "sk-wecom-secret-123456")
    listed = (await http.get(root, headers=as_("boss"))).json()["items"]
    assert [(c["name"], c["settings"]["bases"], c["sessions"]) for c in listed] == [
        ("行政小助手", ["builtin.kb1"], 0)]
    assert (await http.get(root, headers=as_("alice"))).status_code == 403
    # 停用：断开、标成停止
    stopped = (await http.post(f"{root}/{created['id']}/enabled", json={"enabled": False}, headers=as_("boss"))).json()
    assert (stopped["enabled"], stopped["status"]) == (False, "stopped")
    assert not env.channels.supervisor.running(created["id"])
    assert (await http.delete(f"{root}/{created['id']}", headers=as_("boss"))).status_code == 204
    assert (await http.get(root, headers=as_("boss"))).json()["items"] == []


async def test_the_channel_account_holds_exactly_the_chosen_bases(env, http):
    root = f"/api/workspaces/{env.ws}/channels"
    body = {**BODY, "settings": {"answer_mode": "agent", "bases": ["builtin.kb1", "builtin.kb2"]}}
    channel = (await http.post(root, json=body, headers=as_("boss"))).json()
    account = channel["id"]
    profile = await env.users.get(account)
    assert (profile.kind, profile.name, profile.active) == ("channel", "行政助手（企业微信机器人）", True)
    assert env.access.check(account, env.ws, "workspace", "use")
    assert not env.access.check(account, env.ws, "workspace", "edit")
    assert await knowledge_grants(env, account) == [("knowledge_base:builtin.kb1", "use"),
                                                    ("knowledge_base:builtin.kb2", "use")]
    # 渠道账号不能登录，也不出现在加成员的选人结果里
    assert await env.users.authenticate(profile.email, "") is None
    assert all(found.id != account for found in await env.users.search("行政助手"))

    # 改选知识库、改名：授权跟着换，名字跟着改
    moved = {**BODY, "name": "IT 服务台", "secret": None, "settings": {"answer_mode": "agent", "bases": ["builtin.kb3"]}}
    assert (await http.put(f"{root}/{account}", json=moved, headers=as_("boss"))).status_code == 200
    assert await knowledge_grants(env, account) == [("knowledge_base:builtin.kb3", "use")]
    assert (await env.users.get(account)).name == "IT 服务台（企业微信机器人）"

    # 删渠道：移出空间、收回授权、账号禁用（运行记录里还能看到名字）
    assert (await http.delete(f"{root}/{account}", headers=as_("boss"))).status_code == 204
    assert not env.access.check(account, env.ws, "workspace", "use")
    assert await knowledge_grants(env, account) == []
    assert not (await env.users.get(account)).active


async def test_admins_see_who_has_been_asking(env, http):
    root = f"/api/workspaces/{env.ws}/channels"
    created = (await http.post(root, json=BODY, headers=as_("boss"))).json()
    with in_workspace(env.ws):
        channel = await env.channels.admin._store.get(env.ws, created["id"])
    env.conversations.scripts = [answer(1, "一"), answer(1, "二")]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, "报销标准"), connection)
    await env.channels.service.handle(message(channel, "群里问", user="lisi", name="李四", group="chat-9"), connection)
    people = (await http.get(f"{root}/{channel.id}/sessions", headers=as_("boss"))).json()["items"]
    assert sorted((p["display_name"], p["chat_type"], p["chat_id"], p["title"]) for p in people) == [
        ("张三", "direct", "", "报销标准"), ("李四", "group", "chat-9", "群里问")]
    assert (await http.get(root, headers=as_("boss"))).json()["items"][0]["sessions"] == 2
    assert (await http.get(f"{root}/{channel.id}/sessions", headers=as_("alice"))).status_code == 403
