"""公开接口与权限：运行者本人、技能可用性、范围；审批、续跑、取消；事件流。"""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI, Header

from knowwork_server.auth.principal import Principal
from knowwork_server.skills.views import Actor as SkillActor
from knowwork_server.workflows import build_router
from knowwork_server.workflows.events import parse_after, run_snapshots

from .conftest import REVIEW, actor, publish, settle, until

pytestmark = pytest.mark.anyio

BASE = "/api/workspaces/ws1/workflow-runs"


async def principal(x_user: str = Header(), x_scopes: str | None = Header(default=None)) -> Principal:
    if x_scopes is None:
        return Principal(user_id=x_user, auth_method="session")
    return Principal(user_id=x_user, auth_method="api_key", scopes=frozenset(x_scopes.split(",")))


@pytest.fixture
async def client(env):
    app = FastAPI()
    app.include_router(build_router(env.workflows.service, env.access, principal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


def as_(user: str, **extra) -> dict:
    return {"x-user": user, **extra}


async def test_start_view_list_and_owner_only(env, client):
    skill_id = await publish(env)
    started = await client.post(BASE, json={"skillId": skill_id, "inputs": {"contract_id": "c-9"}}, headers=as_("dave"))
    assert started.status_code == 202, started.text
    run_id = started.json()["id"]
    await settle(env, run_id)
    detail = (await client.get(f"{BASE}/{run_id}", headers=as_("dave"))).json()
    assert detail["status"] == "waiting_approval" and detail["inputs"] == {"contract_id": "c-9"}
    assert [s["key"] for s in detail["steps"]] == ["extract", "summarize", "confirm", "save"]
    listed = (await client.get(BASE, headers=as_("dave"))).json()["items"]
    assert [item["id"] for item in listed] == [run_id]
    assert (await client.get(BASE, headers=as_("erin"))).json()["items"] == []
    assert (await client.get(f"{BASE}/{run_id}", headers=as_("erin"))).status_code == 404  # 别人的运行看不到
    for path in ("/resume", "/cancel", "/steps/confirm/decision"):
        body = {"allow": True} if "decision" in path else None
        assert (await client.post(f"{BASE}/{run_id}{path}", json=body, headers=as_("erin"))).status_code == 404
    assert (await client.get(f"{BASE}/{run_id}", headers=as_("mallory"))).status_code == 403  # 非成员
    scoped = await client.get(f"{BASE}/{run_id}", headers=as_("dave", **{"x-scopes": "skills:read"}))
    assert scoped.status_code == 403 and scoped.json()["detail"]["code"] == "forbidden_scope"
    ok = await client.get(f"{BASE}/{run_id}", headers=as_("dave", **{"x-scopes": "workflows:run"}))
    assert ok.status_code == 200


async def test_start_rejections(env, client):
    plain = await publish(env, None, name="no-workflow")
    review = await publish(env, name="review-two")
    cases = [
        ({"skillId": "missing", "inputs": {}}, 404, "skill_not_available"),
        ({"skillId": plain, "inputs": {}}, 422, "not_a_workflow"),
        ({"skillId": review, "inputs": {}}, 422, "invalid_inputs"),
        ({"skillId": review, "inputs": {"contract_id": 1}}, 422, "invalid_inputs"),
        ({"skillId": review, "inputs": {"contract_id": "x" * 300_000}}, 422, "inputs_too_large"),
    ]
    for body, status, code in cases:
        response = await client.post(BASE, json=body, headers=as_("dave"))
        assert (response.status_code, response.json()["detail"]["code"]) == (status, code), body
    assert (await client.post(BASE, json={"skillId": review, "extra": 1}, headers=as_("dave"))).status_code == 422
    # 别人的个人技能对我不可用
    response = await client.post(BASE, json={"skillId": review, "inputs": {"contract_id": "a"}}, headers=as_("erin"))
    assert response.status_code == 404


async def test_workspace_skill_follows_rbac(env, client):
    owner = SkillActor(user_id="alice", workspace_id="ws1")
    from .conftest import workflow_zip

    view = await env.skills.service.install(owner, "workspace", workflow_zip(REVIEW, "shared-review"))
    await env.skills.service.publish(owner, view.id)
    body = {"skillId": view.id, "inputs": {"contract_id": "a"}}
    assert (await client.post(BASE, json=body, headers=as_("dave"))).status_code == 404
    await env.access.grant("alice", "ws1", "user:dave", f"skill:{view.id}", "use")
    assert (await client.post(BASE, json=body, headers=as_("dave"))).status_code == 202


async def test_decide_resume_cancel_over_http(env, client):
    skill_id = await publish(env)
    run_id = (await client.post(BASE, json={"skillId": skill_id, "inputs": {"contract_id": "c"}},
                                headers=as_("dave"))).json()["id"]
    await settle(env, run_id)
    wrong = await client.post(f"{BASE}/{run_id}/steps/extract/decision", json={"allow": True}, headers=as_("dave"))
    assert wrong.status_code == 409 and wrong.json()["detail"]["code"] == "not_waiting_approval"
    assert (await client.post(f"{BASE}/{run_id}/resume", headers=as_("dave"))).status_code == 409
    approved = await client.post(f"{BASE}/{run_id}/steps/confirm/decision", json={"allow": True}, headers=as_("dave"))
    assert approved.status_code == 200
    done = await settle(env, run_id, statuses=("succeeded", "failed"))
    assert done.status == "succeeded"
    again = await client.post(f"{BASE}/{run_id}/cancel", headers=as_("dave"))
    assert again.status_code == 409 and again.json()["detail"]["code"] == "run_finished"

    second = (await client.post(BASE, json={"skillId": skill_id, "inputs": {"contract_id": "d"}},
                                headers=as_("dave"))).json()["id"]
    await settle(env, second)
    cancelled = await client.post(f"{BASE}/{second}/cancel", headers=as_("dave"))
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["steps"][2]["status"] == "cancelled"


async def test_resume_requires_skill_still_available(env, client):
    skill_id = await publish(env)

    async def never(session_id, turn):
        await asyncio.Event().wait()

    original, env.kernel.send = env.kernel.send, never
    run_id = (await env.workflows.service.start(actor(), skill_id, {"contract_id": "c"})).id
    await until(lambda: len(env.kernel.opened) == 1)
    await env.workflows.engine.aclose()  # 关闭：运行被中断
    env.kernel.send = original
    restarted = env.rebuild()
    env.workflows = restarted
    app = FastAPI()
    app.include_router(build_router(restarted.service, env.access, principal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        await env.skills.service.set_enabled(SkillActor(user_id="dave", workspace_id="ws1"), skill_id, False)
        blocked = await http.post(f"{BASE}/{run_id}/resume", headers=as_("dave"))
        assert blocked.status_code == 404
        await env.skills.service.set_enabled(SkillActor(user_id="dave", workspace_id="ws1"), skill_id, True)
        resumed = await http.post(f"{BASE}/{run_id}/resume", headers=as_("dave"))
        assert resumed.status_code == 200
    view = await settle(restarted, run_id)
    assert view.status == "waiting_approval"
    assert await restarted.engine.reconcile(timedelta(seconds=0)) == []


async def test_event_stream_pushes_snapshots_until_finished(env, client):
    skill_id = await publish(env)
    run_id = (await env.workflows.service.start(actor(), skill_id, {"contract_id": "c"})).id
    await settle(env, run_id)
    stream = run_snapshots(env.workflows.store, env.workflows.notifier, "ws1", run_id, 0, poll_seconds=0.05)
    first = json.loads((await anext(stream))["data"])
    assert first["status"] == "waiting_approval"
    await env.workflows.service.decide(actor(), run_id, "confirm", True)
    statuses = [json.loads(item["data"])["status"] async for item in stream]
    assert statuses[-1] == "succeeded"

    response = await client.get(f"{BASE}/{run_id}/events", headers=as_("dave"))
    lines = [line for line in response.text.splitlines() if line.startswith("event:")]
    assert response.status_code == 200 and lines == ["event: workflow.run"]
    version = (await env.workflows.store.load("ws1", run_id)).version
    empty = await client.get(f"{BASE}/{run_id}/events", headers=as_("dave", **{"last-event-id": str(version)}))
    assert "event:" not in empty.text
    assert (await client.get(f"{BASE}/{run_id}/events", headers=as_("erin"))).status_code == 404
    assert parse_after("12", 3) == 12 and parse_after(None, -1) == 0 and parse_after("x", 4) == 4
