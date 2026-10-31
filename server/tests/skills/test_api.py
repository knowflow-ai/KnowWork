"""技能的公开接口与给宿主的内部接口。身份用测试依赖注入，权限走真实的 access 与 require_permission。"""

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from knowwork_server.auth.principal import Principal
from knowwork_server.skills import (
    build_internal_router,
    build_layers_router,
    build_router,
    build_submissions_router,
)

from .conftest import skill_zip

pytestmark = pytest.mark.anyio
ZIP = {"content-type": "application/zip"}
EXTRACT = {"allowed": "extract_records kw_now", "completion": "extract_records"}


async def principal(request: Request) -> Principal:
    user = request.headers.get("x-user")
    if not user:
        raise HTTPException(status_code=401)
    scopes = request.headers.get("x-scopes")
    if scopes is None:
        return Principal(user_id=user, auth_method="session")
    return Principal(user_id=user, auth_method="api_key", scopes=frozenset(filter(None, scopes.split(","))))


@pytest.fixture
async def client(env):
    app = FastAPI()
    app.include_router(build_router(env.skills, env.access, principal))
    app.include_router(build_submissions_router(env.skills.submissions, env.access, principal))
    app.include_router(build_layers_router(env.skills.layers, env.access, principal))
    app.include_router(build_internal_router(env.skills.delivery))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        yield http


def who(user: str, **extra: str) -> dict[str, str]:
    return {"x-user": user, **extra}


async def install(client, user, data, base="/api/workspaces/ws1/skills"):
    return await client.post(base, content=data, headers={**ZIP, **who(user)})


async def test_workspace_skill_lifecycle_over_http(client):
    created = await install(client, "bob", skill_zip(version="1.0.0", **EXTRACT))
    assert created.status_code == 201, created.text
    skill_id = created.json()["id"]
    assert (await install(client, "carol", skill_zip("x"))).status_code == 403
    # 新建的空间技能默认全体成员可用（和 MCP 服务一致），成员能看、不能改
    assert (await client.get(f"/api/workspaces/ws1/skills/{skill_id}", headers=who("dave"))).status_code == 200
    listing = await client.get("/api/workspaces/ws1/skills", headers=who("dave"))
    assert [s["id"] for s in listing.json()["skills"]] == [skill_id]
    assert (await client.post(f"/api/workspaces/ws1/skills/{skill_id}/disable", headers=who("dave"))).status_code == 403

    stale = await client.post(f"/api/workspaces/ws1/skills/{skill_id}/publish", headers={**who("bob"), "if-match": "9"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "revision_conflict"
    published = await client.post(f"/api/workspaces/ws1/skills/{skill_id}/publish", headers=who("bob"),
                                  json={"version": "1.0.1"})
    assert published.status_code == 201 and published.json()["version"] == "1.0.1"

    draft = await client.put(f"/api/workspaces/ws1/skills/{skill_id}/draft", content=skill_zip(body="新", **EXTRACT),
                             headers={**ZIP, **who("bob"), "if-match": '"1"'})
    assert draft.status_code == 200 and draft.json()["draftRevision"] == 2
    detail = (await client.get(f"/api/workspaces/ws1/skills/{skill_id}", headers=who("bob"))).json()
    assert detail["hasUnpublishedChanges"] and [r["version"] for r in detail["releases"]] == ["1.0.1"]

    assert (await client.post(f"/api/workspaces/ws1/skills/{skill_id}/disable", headers=who("bob"))).json()[
        "enabled"] is False
    assert (await client.post(f"/api/workspaces/ws1/skills/{skill_id}/enable", headers=who("bob"))).json()[
        "enabled"] is True
    assert (await client.delete(f"/api/workspaces/ws1/skills/{skill_id}", headers=who("carol"))).status_code == 403
    assert (await client.delete(f"/api/workspaces/ws1/skills/{skill_id}", headers=who("bob"))).status_code == 204
    assert (await client.get(f"/api/workspaces/ws1/skills/{skill_id}", headers=who("bob"))).status_code == 404


async def test_personal_routes_only_touch_own_personal_skills(client):
    created = await install(client, "dave", skill_zip("my-notes"), base="/api/workspaces/ws1/me/skills")
    assert created.status_code == 201
    skill_id = created.json()["id"]
    assert (await client.get(f"/api/workspaces/ws1/me/skills/{skill_id}", headers=who("carol"))).status_code == 404
    assert (await client.get(f"/api/workspaces/ws1/skills/{skill_id}", headers=who("alice"))).status_code == 404
    mine = await client.get("/api/workspaces/ws1/me/skills", headers=who("dave"))
    [listed] = mine.json()["skills"]
    assert (listed["name"], listed["status"], listed["submission"], listed["inactive"]) == ("my-notes", "published",
                                                                                          None, None)
    publish = await client.post(f"/api/workspaces/ws1/me/skills/{skill_id}/publish", headers=who("dave"))
    assert publish.status_code in (404, 405), "个人技能上传即发布，没有发布接口"
    assert (await client.get("/api/workspaces/ws1/me/skills", headers=who("mallory"))).status_code == 403


async def test_submit_and_review_over_http(client):
    me = "/api/workspaces/ws1/me/skills"
    skill_id = (await install(client, "dave", skill_zip("my-notes", version="1.0.0"), base=me)).json()["id"]
    submitted = await client.post(f"{me}/{skill_id}/submission", headers=who("dave"), json={"note": "部门都用得上"})
    assert submitted.status_code == 201 and submitted.json()["status"] == "pending"
    sub_id = submitted.json()["id"]
    assert (await client.get("/api/workspaces/ws1/skill-submissions", headers=who("dave"))).status_code == 403
    listed = (await client.get("/api/workspaces/ws1/skill-submissions", headers=who("bob"))).json()
    assert [s["id"] for s in listed["submissions"]] == [sub_id]
    detail = (await client.get(f"/api/workspaces/ws1/skill-submissions/{sub_id}", headers=who("bob"))).json()
    assert (detail["content"], detail["target"], detail["nameTaken"]) == ("按步骤抽取合同字段。", None, False)
    archive = await client.get(f"/api/workspaces/ws1/skill-submissions/{sub_id}/archive", headers=who("bob"))
    assert archive.status_code == 200 and archive.headers["content-type"] == "application/zip"
    assert "my-notes-1.0.0.zip" in archive.headers["content-disposition"]

    reject = await client.post(f"/api/workspaces/ws1/skill-submissions/{sub_id}/reject", headers=who("bob"),
                               json={"reason": ""})
    assert reject.status_code == 422
    approved = await client.post(f"/api/workspaces/ws1/skill-submissions/{sub_id}/approve", headers=who("bob"),
                                 json={"publish": True})
    assert approved.status_code == 200 and approved.json()["status"] == "published"
    again = await client.post(f"/api/workspaces/ws1/skill-submissions/{sub_id}/approve", headers=who("bob"))
    assert again.status_code == 409 and again.json()["detail"]["code"] == "submission_closed"
    [mine] = (await client.get(me, headers=who("dave"))).json()["skills"]
    assert mine["submission"]["status"] == "approved"
    assert mine["submission"]["targetSkillId"] == approved.json()["id"]


async def test_withdraw_and_available_over_http(client):
    me = "/api/workspaces/ws1/me/skills"
    skill_id = (await install(client, "dave", skill_zip("my-notes"), base=me)).json()["id"]
    assert (await client.delete(f"{me}/{skill_id}/submission", headers=who("dave"))).status_code == 409
    await client.post(f"{me}/{skill_id}/submission", headers=who("dave"))
    assert (await client.delete(f"{me}/{skill_id}/submission", headers=who("dave"))).status_code == 204
    assert (await client.post(f"{me}/{skill_id}/submission", headers=who("carol"))).status_code == 404
    available = (await client.get(f"{me}/available", headers=who("dave"))).json()["skills"]
    assert [(s["name"], s["source"]) for s in available] == [("my-notes", "personal")]
    read_only = who("dave", **{"x-scopes": "skills:read"})
    assert (await client.post(f"{me}/{skill_id}/submission", headers=read_only)).status_code == 403


async def test_request_body_checks(client):
    bad_type = await client.post("/api/workspaces/ws1/skills", content=b"x",
                                 headers={**who("bob"), "content-type": "text/plain"})
    assert bad_type.status_code == 415
    invalid = await install(client, "bob", b"not a zip")
    assert invalid.status_code == 422 and invalid.json()["detail"]["code"] == "invalid_zip"
    too_big = await client.post("/api/workspaces/ws1/skills", content=b"x" * 10,
                                headers={**ZIP, **who("bob"), "content-length": str(200 * 1024 * 1024)})
    assert too_big.status_code == 413
    created = (await install(client, "bob", skill_zip())).json()
    bad_match = await client.put(f"/api/workspaces/ws1/skills/{created['id']}/draft", content=skill_zip(),
                                 headers={**ZIP, **who("bob"), "if-match": "abc"})
    assert bad_match.status_code == 400


async def test_api_key_scopes(client):
    denied = await install(client, "bob", skill_zip(), base="/api/workspaces/ws1/skills")
    assert denied.status_code == 201
    read_only = who("bob", **{"x-scopes": "skills:read"})
    assert (await client.get("/api/workspaces/ws1/skills", headers=read_only)).status_code == 200
    write = await client.post("/api/workspaces/ws1/skills", content=skill_zip("other"), headers={**ZIP, **read_only})
    assert write.status_code == 403
    nothing = who("bob", **{"x-scopes": ""})
    assert (await client.get("/api/workspaces/ws1/skills", headers=nothing)).status_code == 403


async def test_internal_catalog_and_content(env, client):
    created = (await install(client, "dave", skill_zip("my-notes", body="正文"), base="/api/workspaces/ws1/me/skills"))
    await client.post(f"/api/workspaces/ws1/me/skills/{created.json()['id']}/publish", headers=who("dave"))
    token = env.skills.delivery.issue_session_token("ws1", "sess-1", "dave")
    auth = {"authorization": f"Bearer {token}"}
    catalog = await client.get("/internal/extensions/skills/catalog", headers=auth)
    assert catalog.status_code == 200 and catalog.json()["bundledOff"] == []
    [entry] = catalog.json()["skills"]
    assert entry["name"] == "my-notes" and entry["unlocks"] == []
    content = await client.get("/internal/extensions/skills/content",
                               params={"name": "my-notes", "digest": entry["digest"]}, headers=auth)
    assert content.status_code == 200 and content.json()["content"] == "正文"
    other = env.skills.delivery.issue_session_token("ws1", "sess-2", "carol")
    hidden = await client.get("/internal/extensions/skills/content", params={"name": "my-notes", "digest": entry["digest"]},
                              headers={"authorization": f"Bearer {other}"})
    assert hidden.status_code == 404 and hidden.json()["error"]["code"] == "skill_not_found"
    for headers in ({}, {"authorization": "Bearer nope"}, {"authorization": "Basic x"}):
        assert (await client.get("/internal/extensions/skills/catalog", headers=headers)).status_code == 401
        response = await client.get("/internal/extensions/skills/content",
                                    params={"name": "my-notes", "digest": entry["digest"]}, headers=headers)
        assert response.status_code == 401
    bad = await client.get("/internal/extensions/skills/content", params={"name": "../x", "digest": "1"}, headers=auth)
    assert bad.status_code == 422


async def test_platform_and_bundled_here_over_http(env, client):
    await env.access.create_workspace(workspace_id="default", name="默认空间", owner_user_id="alice")
    env.access.set_system_admin("root")
    shared = (await install(client, "bob", skill_zip("summary"))).json()
    await client.post(f"/api/workspaces/ws1/skills/{shared['id']}/publish", headers=who("bob"))
    view = await env.skills.platform.install("root", skill_zip("summary"))
    await env.skills.platform.publish("root", view.id)
    await env.skills.platform.set_audience("root", view.id, ["*"])
    listed = (await client.get("/api/workspaces/ws1/skills", headers=who("bob"))).json()["skills"]
    assert [(s["name"], s["inactive"]) for s in listed] == [("summary", "name_taken")], "被同名的公共技能顶掉"

    base = "/api/workspaces/ws1/platform-skills"
    assert (await client.get(base, headers=who("carol"))).status_code == 403
    [upper] = (await client.get(base, headers=who("bob"))).json()["skills"]
    assert (upper["key"], upper["source"], upper["off"]) == (f"platform:{view.id}", "platform", False)
    off = await client.post(f"{base}/disable", headers=who("bob"), json={"key": "bundled:office-docx"})
    assert off.status_code == 204
    token = env.skills.delivery.issue_session_token("ws1", "sess-9", "carol")
    catalog = await client.get("/internal/extensions/skills/catalog", headers={"authorization": f"Bearer {token}"})
    assert catalog.json()["bundledOff"] == ["office-docx"]
    assert [s["scope"] for s in catalog.json()["skills"]] == ["platform"]
    bad = await client.post(f"{base}/disable", headers=who("bob"), json={"key": "nope"})
    assert bad.status_code == 422

    # 收窄给谁用：只给 dave；放回全体成员
    users = f"{base}/users"
    narrow = await client.put(users, headers=who("bob"), json={"key": upper["key"], "subjects": ["user:dave"]})
    assert narrow.json() == {"key": upper["key"], "narrowed": True}
    [upper] = [s for s in (await client.get(base, headers=who("bob"))).json()["skills"] if s["source"] == "platform"]
    assert (upper["narrowed"], upper["resource"]) == (True, f"skill:{view.id}")
    catalog = await client.get("/internal/extensions/skills/catalog", headers={"authorization": f"Bearer {token}"})
    assert [s["scope"] for s in catalog.json()["skills"]] == ["workspace"], "carol 用不了企业版，同名的空间技能生效"
    assert (await client.put(users, headers=who("carol"), json={"key": upper["key"], "subjects": []})).status_code == 403
    unknown = await client.put(users, headers=who("bob"), json={"key": upper["key"], "subjects": ["dept:nope"]})
    assert unknown.status_code == 422
    wide = await client.put(users, headers=who("bob"), json={"key": upper["key"], "subjects": ["role:editor"]})
    assert wide.json()["narrowed"] is False


async def test_env_values_over_http(client):
    import io
    import zipfile

    md = "---\nname: report-sync\ndescription: 同步报表\nenv:\n  REPORT_API_KEY required 密钥\n---\n\n$REPORT_API_KEY\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("SKILL.md", md)
    created = (await install(client, "bob", buffer.getvalue())).json()
    path = f"/api/workspaces/ws1/skills/{created['id']}/env"
    assert (await client.put(path, json={"values": {"REPORT_API_KEY": "sk"}}, headers=who("carol"))).status_code == 403
    done = await client.put(path, json={"values": {"REPORT_API_KEY": "sk-live-123456"}}, headers=who("bob"))
    assert done.status_code == 200 and done.json()["envFilled"] == ["REPORT_API_KEY"]
    assert "sk-live" not in done.text
    bad = await client.put(path, json={"values": {"lower": "x"}}, headers=who("bob"))
    assert bad.status_code == 422
    mine = (await install(client, "dave", buffer.getvalue(), base="/api/workspaces/ws1/me/skills")).json()
    personal = await client.put(f"/api/workspaces/ws1/me/skills/{mine['id']}/env",
                                json={"values": {"REPORT_API_KEY": "dave-key"}}, headers=who("dave"))
    assert personal.status_code == 200 and personal.json()["envFilled"] == ["REPORT_API_KEY"]
