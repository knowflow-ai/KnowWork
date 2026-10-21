"""技能的公开接口与给宿主的内部接口。身份用测试依赖注入，权限走真实的 access 与 require_permission。"""

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request

from knowwork_server.auth.principal import Principal
from knowwork_server.skills import build_internal_router, build_router

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
    app.include_router(build_router(env.skills.service, env.access, principal))
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
    assert (await client.get(f"/api/workspaces/ws1/skills/{skill_id}", headers=who("dave"))).status_code == 403
    listing = await client.get("/api/workspaces/ws1/skills", headers=who("dave"))
    assert listing.json() == {"skills": []}

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
    assert [s["name"] for s in mine.json()["skills"]] == ["my-notes"]
    publish = await client.post(f"/api/workspaces/ws1/me/skills/{skill_id}/publish", headers=who("dave"))
    assert publish.status_code == 201
    assert (await client.get("/api/workspaces/ws1/me/skills", headers=who("mallory"))).status_code == 403


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
    assert catalog.status_code == 200
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
