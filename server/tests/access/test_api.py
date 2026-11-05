"""HTTP 接口：调用者经注入的依赖得到；权限在服务端判定，越权 403、不存在 404、冲突 409、非法 422。"""

from typing import Annotated

import httpx
import pytest
from fastapi import Depends, FastAPI, Header, HTTPException

from knowwork_server.access.api import build_router
from knowwork_server.access.deps import require_permission
from knowwork_server.auth.principal import Principal

pytestmark = pytest.mark.anyio


async def principal(x_user: Annotated[str | None, Header()] = None) -> Principal:
    if not x_user:
        raise HTTPException(status_code=401, detail="未登录")
    return Principal(user_id=x_user, auth_method="session")


@pytest.fixture
async def client(service, ws):
    app = FastAPI()
    app.include_router(build_router(service, principal))
    guard = require_permission(service, principal, lambda p: f"skill:{p['skill_id']}", "use")

    @app.get("/api/workspaces/{workspace_id}/skills/{skill_id}")
    async def skill(workspace_id: str, skill_id: str, who: Annotated[Principal, Depends(guard)]):
        return {"id": skill_id, "by": who.user_id}

    @app.get("/broken/{skill_id}")
    async def broken(skill_id: str, who: Annotated[Principal, Depends(guard)]):
        return {}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        yield http


def as_(user):
    return {"x-user": user}


async def test_members_endpoints(client):
    base = "/api/workspaces/ws1/members"
    assert (await client.get(base)).status_code == 401
    created = await client.post(base, json={"user_id": "erin", "role": "editor"}, headers=as_("bob"))
    assert created.status_code == 201 and created.json() == {"user_id": "erin", "role": "editor", "origin": "manual"}
    assert (await client.post(base, json={"user_id": "erin", "role": "editor"}, headers=as_("bob"))).status_code == 409
    assert (await client.post(base, json={"user_id": "x", "role": "god"}, headers=as_("bob"))).status_code == 422
    assert (await client.post(base, json={"user_id": "x", "role": "editor"}, headers=as_("dave"))).status_code == 403
    patched = await client.patch(f"{base}/erin", json={"role": "editor"}, headers=as_("bob"))
    assert patched.json()["role"] == "editor"
    assert (await client.delete(f"{base}/erin", headers=as_("bob"))).status_code == 204
    assert (await client.delete(f"{base}/erin", headers=as_("bob"))).status_code == 404
    listed = await client.get(base, headers=as_("dave"))
    assert [m["user_id"] for m in listed.json()] == ["alice", "bob", "carol", "dave"]
    assert (await client.get("/api/workspaces/other/members", headers=as_("alice"))).status_code == 403


async def test_department_and_group_endpoints(client):
    base = "/api/workspaces/ws1"
    root = (await client.post(f"{base}/departments", json={"name": "总部"}, headers=as_("bob"))).json()["id"]
    child = (await client.post(f"{base}/departments", json={"name": "法务部", "parent_id": root},
                               headers=as_("bob"))).json()["id"]
    assert (await client.post(f"{base}/departments/{child}/members", json={"user_id": "dave", "is_primary": True},
                              headers=as_("bob"))).status_code == 204
    tree = (await client.get(f"{base}/departments", headers=as_("dave"))).json()
    assert tree[0]["children"][0]["members"] == [["dave", True]]
    assert (await client.patch(f"{base}/departments/{child}", json={"move": True, "parent_id": None},
                               headers=as_("bob"))).status_code == 204
    assert (await client.patch(f"{base}/departments/{root}", json={"name": "集团"},
                               headers=as_("bob"))).status_code == 204
    assert len((await client.get(f"{base}/departments", headers=as_("dave"))).json()) == 2
    assert (await client.delete(f"{base}/departments/{child}/members/dave", headers=as_("bob"))).status_code == 204
    assert (await client.delete(f"{base}/departments/{child}", headers=as_("bob"))).status_code == 204
    assert (await client.delete(f"{base}/departments/{child}", headers=as_("dave"))).status_code == 403

    group = (await client.post(f"{base}/groups", json={"name": "客服"}, headers=as_("bob"))).json()["id"]
    assert (await client.patch(f"{base}/groups/{group}", json={"name": "售后"}, headers=as_("bob"))).status_code == 204
    assert (await client.post(f"{base}/groups/{group}/members", json={"user_id": "dave"},
                              headers=as_("bob"))).status_code == 204
    groups = (await client.get(f"{base}/groups", headers=as_("carol"))).json()
    assert groups == [{"id": group, "name": "售后", "members": ["dave"]}]
    assert (await client.delete(f"{base}/groups/{group}/members/dave", headers=as_("bob"))).status_code == 204
    assert (await client.delete(f"{base}/groups/{group}", headers=as_("bob"))).status_code == 204


async def test_grant_endpoints_and_guard(client):
    base = "/api/workspaces/ws1"
    skill = f"{base}/skills/s1"
    assert (await client.get(skill, headers=as_("dave"))).status_code == 403
    body = {"subject": "user:dave", "resource": "skill:s1", "action": "use"}
    assert (await client.post(f"{base}/grants", json=body, headers=as_("carol"))).status_code == 403
    assert (await client.post(f"{base}/grants", json=body, headers=as_("bob"))).status_code == 201
    assert (await client.get(skill, headers=as_("dave"))).json() == {"id": "s1", "by": "dave"}
    listed = (await client.get(f"{base}/grants", params={"resource": "skill:s1"}, headers=as_("bob"))).json()
    assert listed == [body]
    visible = (await client.get(f"{base}/me/visible", params={"resource_type": "skill"}, headers=as_("dave"))).json()
    assert visible == {"all": False, "ids": ["s1"]}
    check = await client.get(f"{base}/me/check", params={"resource": "skill:s1"}, headers=as_("dave"))
    assert check.json() == {"allowed": True}
    revoke = await client.delete(f"{base}/grants", params={"subject": "user:dave", "resource": "skill:s1"},
                                 headers=as_("bob"))
    assert revoke.status_code == 204
    assert (await client.get(skill, headers=as_("dave"))).status_code == 403
    assert (await client.get("/broken/s1", headers=as_("alice"))).status_code == 403  # 缺 workspace_id 也拒绝
    bad = {"subject": "user:dave", "resource": "workspace", "action": "use"}
    assert (await client.post(f"{base}/grants", json=bad, headers=as_("bob"))).status_code == 422


async def test_grant_several_subjects_and_resources_at_once(client):
    """授权页多选：几个对象 × 几个资源一次授上；有一个对象不对就都不授。"""
    base = "/api/workspaces/ws1"
    body = {"subjects": ["user:dave", "user:carol", "user:dave"], "resources": ["skill:s2", "skill:s4"],
            "action": "use"}
    assert (await client.post(f"{base}/grants/batch", json=body, headers=as_("carol"))).status_code == 403
    done = await client.post(f"{base}/grants/batch", json=body, headers=as_("bob"))
    assert done.status_code == 201
    assert [(g["subject"], g["resource"]) for g in done.json()["grants"]] == [  # 重复的只授一次
        ("user:dave", "skill:s2"), ("user:carol", "skill:s2"), ("user:dave", "skill:s4"), ("user:carol", "skill:s4")]
    assert (await client.get(f"{base}/skills/s4", headers=as_("dave"))).status_code == 200
    # 再授一次就是改档
    edit = {**body, "subjects": ["user:dave"], "resources": ["skill:s2"], "action": "edit"}
    assert (await client.post(f"{base}/grants/batch", json=edit, headers=as_("bob"))).status_code == 201
    listed = (await client.get(f"{base}/grants", params={"resource": "skill:s2"}, headers=as_("bob"))).json()
    assert sorted((g["subject"], g["action"]) for g in listed) == [("user:carol", "use"), ("user:dave", "edit")]
    # 有一个不是本空间的人：整批不授
    mixed = {"subjects": ["role:editor", "user:stranger"], "resources": ["skill:s3"], "action": "use"}
    assert (await client.post(f"{base}/grants/batch", json=mixed, headers=as_("bob"))).status_code in (404, 422)
    assert (await client.get(f"{base}/grants", params={"resource": "skill:s3"}, headers=as_("bob"))).json() == []
    for empty in ({"subjects": [], "resources": ["skill:s3"], "action": "use"},
                  {"subjects": ["user:dave"], "resources": [], "action": "use"}):
        assert (await client.post(f"{base}/grants/batch", json=empty, headers=as_("bob"))).status_code == 422


async def test_granting_a_lower_tier_to_admins_keeps_their_default(client):
    """给「管理员」角色授全部技能的「使用」：系统给管理员的默认「管理」不能被当成旧档删掉。"""
    base = "/api/workspaces/ws1"
    body = {"subjects": ["role:admin"], "resources": ["skill:*"], "action": "use"}
    assert (await client.post(f"{base}/grants/batch", json=body, headers=as_("alice"))).status_code == 201
    # bob 是管理员：照样能管技能（改授权要 edit 以上）
    again = {"subjects": ["user:dave"], "resources": ["skill:s9"], "action": "use"}
    assert (await client.post(f"{base}/grants/batch", json=again, headers=as_("bob"))).status_code == 201
    check = await client.get(f"{base}/me/check", params={"resource": "skill:s9", "action": "owner"},
                             headers=as_("bob"))
    assert check.json() == {"allowed": True}
    # 撤掉另授的这一档：默认的「管理」还在
    revoke = await client.delete(f"{base}/grants", params={"subject": "role:admin", "resource": "skill:*"},
                                 headers=as_("alice"))
    assert revoke.status_code == 204
    check = await client.get(f"{base}/me/check", params={"resource": "skill:s9", "action": "owner"},
                             headers=as_("bob"))
    assert check.json() == {"allowed": True}
    # 再撤就没有可撤的了（默认权限不算授权）
    again = await client.delete(f"{base}/grants", params={"subject": "role:admin", "resource": "skill:*"},
                                headers=as_("alice"))
    assert again.status_code == 404
    # 授的正好和默认一样：不重复写，列表里也不出现
    same = {"subjects": ["role:admin"], "resources": ["skill:*"], "action": "owner"}
    assert (await client.post(f"{base}/grants/batch", json=same, headers=as_("alice"))).status_code == 201
    listed = (await client.get(f"{base}/grants", params={"resource": "skill:*"}, headers=as_("alice"))).json()
    assert [g for g in listed if g["subject"] == "role:admin"] == []


class _Profile:
    def __init__(self, name: str, email: str, kind: str = "person") -> None:
        self.name, self.email, self.kind = name, email, kind


async def _client_with(service, profiles):
    app = FastAPI()
    app.include_router(build_router(service, principal, profiles=profiles))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


async def test_member_list_carries_name_and_email_when_profiles_are_wired(service, ws):
    asked = []

    async def profiles(ids):
        asked.append(tuple(ids))
        return {"alice": _Profile("爱丽丝", "alice@corp.cn"), "carol": _Profile("行政助手", "x@channel.invalid", "channel")}

    async with await _client_with(service, profiles) as http:
        listed = (await http.get("/api/workspaces/ws1/members", headers=as_("dave"))).json()
    assert asked == [("alice", "bob", "carol", "dave")]
    assert listed[0] == {"user_id": "alice", "role": "owner", "origin": "manual", "name": "爱丽丝",
                         "email": "alice@corp.cn", "kind": "person"}
    assert listed[1] == {"user_id": "bob", "role": "admin", "origin": "manual", "name": None, "email": None,
                         "kind": "person"}
    assert listed[2]["kind"] == "channel"  # 渠道账号带上种类，前端从成员页滤掉


async def test_member_list_survives_a_failing_profile_lookup(service, ws):
    async def broken(ids):
        raise RuntimeError("用户库不可用")

    async with await _client_with(service, broken) as http:
        response = await http.get("/api/workspaces/ws1/members", headers=as_("dave"))
    assert response.status_code == 200
    assert response.json()[0] == {"user_id": "alice", "role": "owner", "origin": "manual", "name": None,
                                  "email": None, "kind": "person"}


async def test_member_list_without_profiles_keeps_the_old_shape(client, ws):
    listed = (await client.get("/api/workspaces/ws1/members", headers=as_("dave"))).json()
    assert listed[0] == {"user_id": "alice", "role": "owner", "origin": "manual"}


async def test_enterprise_org_endpoints(client, service, ws):
    from knowwork_server.access.enterprise import OrgDepartmentInput as Dept
    from knowwork_server.access.enterprise import OrgMembershipInput as Member

    base = "/api/workspaces/ws1"
    assert (await client.get(f"{base}/org-departments", headers=as_("dave"))).json() == []  # 没接目录时为空
    await service.replace_org("ad", (Dept("rnd", None, "研发中心"), Dept("app", "rnd", "应用组")),
                              (Member("erin", "app", True),))
    departments = (await client.get(f"{base}/org-departments", headers=as_("dave"))).json()
    app = next(d for d in departments if d["external_id"] == "app")
    assert app["name"] == "应用组" and app["member_count"] == 1
    rule = {"department_id": app["parent_id"], "include_children": True, "role": "editor"}
    assert (await client.post(f"{base}/member-rules", json=rule, headers=as_("dave"))).status_code == 403
    created = await client.post(f"{base}/member-rules", json=rule, headers=as_("bob"))
    assert created.status_code == 201 and created.json()["department_name"] == "研发中心"
    assert (await client.post(f"{base}/member-rules", json=rule, headers=as_("bob"))).status_code == 409
    members = (await client.get(f"{base}/members", headers=as_("bob"))).json()
    assert {"user_id": "erin", "role": "editor", "origin": "rule"} in members
    assert (await client.delete(f"{base}/members/erin", headers=as_("bob"))).status_code == 409
    listed = (await client.get(f"{base}/member-rules", headers=as_("bob"))).json()
    assert [r["id"] for r in listed] == [created.json()["id"]]
    assert (await client.delete(f"{base}/member-rules/{listed[0]['id']}", headers=as_("bob"))).status_code == 204
    assert "erin" not in {m["user_id"] for m in (await client.get(f"{base}/members", headers=as_("bob"))).json()}
    missing = {**rule, "department_id": "od_missing"}
    assert (await client.post(f"{base}/member-rules", json=missing, headers=as_("bob"))).status_code == 404


async def test_org_department_members_for_the_space_admin(service, ws):
    """组织架构里一个部门的人：只给本空间管理员看，带姓名、主部门、在不在本空间，不给邮箱。"""
    from knowwork_server.access.enterprise import OrgDepartmentInput as Dept
    from knowwork_server.access.enterprise import OrgMembershipInput as Member

    await service.replace_org("ad", (Dept("rnd", None, "研发中心"), Dept("app", "rnd", "应用组")),
                              (Member("erin", "app", True), Member("bob", "app")))
    names = {"erin": _Profile("艾琳", "erin@corp.cn"), "bob": _Profile("鲍勃", "bob@corp.cn")}

    async def profiles(ids):
        return {user_id: names[user_id] for user_id in ids if user_id in names}

    async with await _client_with(service, profiles) as http:
        base = "/api/workspaces/ws1"
        app = next(d for d in (await http.get(f"{base}/org-departments", headers=as_("bob"))).json()
                   if d["external_id"] == "app")
        url = f"{base}/org-departments/{app['id']}/members"
        assert (await http.get(url, headers=as_("dave"))).status_code == 403
        listed = (await http.get(url, headers=as_("bob"))).json()
        assert listed == [{"user_id": "bob", "is_primary": False, "role": "admin", "name": "鲍勃"},
                          {"user_id": "erin", "is_primary": True, "role": None, "name": "艾琳"}]
        assert (await http.get(f"{base}/org-departments/{app['parent_id']}/members", headers=as_("bob"))
                ).json() == []  # 只这一级，不含下级
        assert (await http.get(f"{base}/org-departments/od_missing/members", headers=as_("bob"))).status_code == 404
