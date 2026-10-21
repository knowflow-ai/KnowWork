"""业务服务侧工具接口：令牌、可用性重新判定、参数校验、动作回执的幂等与截断。真实 PostgreSQL。"""

import uuid
from datetime import datetime

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

import knowwork_server.tools.models  # noqa: F401  注册表结构
from knowwork_server.db.base import Base, make_engine, make_sessionmaker
from knowwork_server.kernel.tokens import TokenError, TokenSigner, derive_key
from knowwork_server.tools import build_tool_service
from knowwork_server.tools.api import build_internal_router
from knowwork_server.tools.models import ActionReceipt, ActionResultBlob
from knowwork_server.tools.registry import KW_NOW, BuiltinTool, StaticToolRegistry, ToolFailure, ToolSpec
from knowwork_server.tools.tokens import ToolTokenSigner

pytestmark = pytest.mark.anyio
MASTER = "m" * 40
PREFIX = "/internal/extensions/tools"


class Counting:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.contexts: list = []

    async def __call__(self, ctx, arguments):
        self.calls.append(arguments)
        self.contexts.append(ctx)
        if arguments.get("q") == "boom":
            raise RuntimeError("数据库密码是 hunter2")
        if arguments.get("q") == "refuse":
            raise ToolFailure("台账已锁定，不能写入")
        if arguments.get("q") == "big":
            return "x" * (70 * 1024)
        return f"已记录：{arguments['q']}"


NOTE_SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"],
               "additionalProperties": False}


@pytest.fixture
async def env(database_url):
    engine = make_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)
    handler = Counting()
    registry = StaticToolRegistry.of([KW_NOW, BuiltinTool(ToolSpec("kw_note", "记一笔", NOTE_SCHEMA, "write"), handler)])
    service = build_tool_service(MASTER, sessionmaker, registry=registry)
    app = FastAPI()
    app.include_router(build_internal_router(service))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://internal")
    session_id = f"conv-{uuid.uuid4().hex[:8]}"
    token = await service.issue_session_token("ws1", session_id, user_id="u1")
    yield {"client": client, "service": service, "registry": registry, "handler": handler,
           "sessionmaker": sessionmaker, "session_id": session_id, "token": token}
    await client.aclose()
    await engine.dispose()


def call(env, q, *, call_id="call-1", agent=None, token=None):
    agent = agent or env["session_id"]
    return env["client"].post(f"{PREFIX}/execute",
                              json={"toolName": "kw_note", "arguments": {"q": q}, "agentSessionId": agent, "callId": call_id},
                              headers={"authorization": f"Bearer {token or env['token']}", "idempotency-key": f"{agent}:{call_id}"})


def test_approval_defaults_follow_risk_and_can_be_set_explicitly():
    """审批与风险分开：不写 approval 时按风险推导（只读不问、写入与高风险要问），也可以显式声明。"""
    def spec(risk, approval=None, guidance=None):
        return ToolSpec("kw_x", "x", NOTE_SCHEMA, risk, approval=approval, guidance=guidance)

    assert [spec(r).approval_mode for r in ("read", "write", "dangerous")] == ["never", "ask", "ask"]
    assert spec("dangerous", "never").describe()["approval"] == "never"
    assert spec("dangerous", "never").describe()["risk"] == "dangerous", "免审批不能改写真实风险等级"
    assert spec("read", "ask").approval_mode == "ask"
    assert spec("read", guidance="交付规则").describe()["guidance"] == "交付规则"
    with pytest.raises(ValueError):
        spec("read", "sometimes")
    with pytest.raises(ValueError):
        spec("unknown")
    with pytest.raises(ValueError):
        spec("read", guidance="x" * 1001)


def test_tool_token_round_trip_and_isolation():
    signer = ToolTokenSigner.from_master_key(MASTER)
    token = signer.issue(workspace_id="ws1", session_id="s1", user_id="u1", tools=["b", "a", "a"], ttl_seconds=60,
                         now=1000)
    claims = signer.verify(token, now=1030)
    assert (claims.workspace_id, claims.session_id, claims.user_id, claims.tools) == ("ws1", "s1", "u1",
                                                                                    frozenset({"a", "b"}))
    with pytest.raises(TokenError):
        signer.verify(token, now=1060)
    with pytest.raises(ValueError):
        signer.issue(workspace_id="ws1", session_id="s1", user_id="", tools=[], ttl_seconds=60)
    with pytest.raises(TokenError):
        signer.verify(token[:-2] + ("AA" if not token.endswith("AA") else "BB"), now=1001)
    model_token = TokenSigner(derive_key(MASTER, "host-callback")).issue(audience="tools", workspace_id="ws1", ttl_seconds=60)
    with pytest.raises(TokenError):
        signer.verify(model_token)  # 用途子密钥不同，别的令牌冒充不了


async def test_list_returns_session_tools(env):
    response = await env["client"].get(f"{PREFIX}/list", headers={"authorization": f"Bearer {env['token']}"})
    assert response.status_code == 200
    tools = {t["name"]: t for t in response.json()["tools"]}
    assert set(tools) == {"kw_now", "kw_note"}
    assert tools["kw_note"]["risk"] == "write"
    assert (tools["kw_note"]["approval"], tools["kw_now"]["approval"]) == ("ask", "never")
    assert "guidance" not in tools["kw_note"]
    assert tools["kw_note"]["inputSchema"] == NOTE_SCHEMA
    assert (await env["client"].get(f"{PREFIX}/list")).status_code == 401


async def test_execute_writes_receipt_and_replays_without_rerunning(env):
    first = await call(env, "第一笔")
    assert first.status_code == 200, first.text
    assert first.json()["content"] == "已记录：第一笔"
    assert first.json()["replayed"] is False
    again = await call(env, "第一笔")
    assert again.json()["replayed"] is True
    assert again.json()["actionId"] == first.json()["actionId"]
    assert again.json()["content"] == "已记录：第一笔"
    assert len(env["handler"].calls) == 1, "同一调用被执行了两次"
    ctx = env["handler"].contexts[0]
    assert (ctx.workspace_id, ctx.session_id, ctx.user_id) == ("ws1", env["session_id"], "u1")
    async with env["sessionmaker"]() as session:
        rows = (await session.execute(select(ActionReceipt).where(ActionReceipt.session_id == env["session_id"]))).scalars().all()
    assert len(rows) == 1
    assert rows[0].status == "succeeded"
    assert rows[0].idempotency_key == f"{env['session_id']}|{env['session_id']}:call-1"
    other = await call(env, "第二笔", call_id="call-2")
    assert other.json()["actionId"] != first.json()["actionId"]
    assert len(env["handler"].calls) == 2


async def test_same_call_id_with_other_arguments_is_rejected(env):
    await call(env, "原始")
    conflict = await call(env, "篡改")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    assert len(env["handler"].calls) == 1


async def test_availability_is_rejudged_at_execution(env):
    narrow = env["service"]._signer.issue(workspace_id="ws1", session_id=env["session_id"], user_id="u1",
                                         tools=["kw_now"], ttl_seconds=60)
    denied = await call(env, "x", token=narrow)
    assert denied.status_code == 403
    env["registry"].tools.pop("kw_note")
    removed = await call(env, "x", call_id="call-9")
    assert removed.status_code == 403
    assert "已不在" in removed.json()["error"]["message"]
    assert env["handler"].calls == []


async def test_input_validation(env):
    client, headers = env["client"], {"authorization": f"Bearer {env['token']}"}
    bad_args = await client.post(f"{PREFIX}/execute", json={"toolName": "kw_note", "arguments": {"q": 1}, "agentSessionId": env["session_id"], "callId": "c"},
                                 headers={**headers, "idempotency-key": f"{env['session_id']}:c"})
    assert bad_args.status_code == 400
    assert bad_args.json()["error"]["code"] == "invalid_arguments"
    mismatch = await client.post(f"{PREFIX}/execute", json={"toolName": "kw_note", "arguments": {"q": "x"}, "agentSessionId": env["session_id"], "callId": "c"},
                                 headers={**headers, "idempotency-key": "other:c"})
    assert mismatch.status_code == 400
    extra = await client.post(f"{PREFIX}/execute", json={"toolName": "kw_note", "agentSessionId": "s", "callId": "c", "x": 1},
                              headers={**headers, "idempotency-key": "s:c"})
    assert extra.status_code == 400
    assert (await call(env, "x", token="nope")).status_code == 401
    assert env["handler"].calls == []


async def test_failures_are_recorded_without_leaking_internals(env):
    refused = await call(env, "refuse", call_id="r1")
    assert refused.json()["status"] == "failed"
    assert refused.json()["content"] == "台账已锁定，不能写入"
    crashed = await call(env, "boom", call_id="r2")
    assert crashed.json()["status"] == "failed"
    assert "hunter2" not in crashed.text
    assert (await call(env, "boom", call_id="r2")).json()["replayed"] is True
    assert len(env["handler"].calls) == 2


async def test_large_result_is_stored_by_reference_and_truncated(env):
    response = await call(env, "big", call_id="big-1")
    body = response.json()
    assert body["truncated"] is True
    assert len(body["content"].encode()) <= 16 * 1024
    assert body["resultRef"]
    async with env["sessionmaker"]() as session:
        blob = await session.get(ActionResultBlob, body["resultRef"])
        receipt = (await session.execute(select(ActionReceipt).where(ActionReceipt.action_id == body["actionId"]))).scalar_one()
    assert len(blob.content) == 70 * 1024
    assert receipt.truncated is True and receipt.result_ref == body["resultRef"]


async def test_kw_now_returns_current_time(env):
    response = await env["client"].post(f"{PREFIX}/execute",
                                        json={"toolName": "kw_now", "agentSessionId": env["session_id"], "callId": "t"},
                                        headers={"authorization": f"Bearer {env['token']}", "idempotency-key": f"{env['session_id']}:t"})
    assert response.json()["status"] == "succeeded"
    assert datetime.fromisoformat(response.json()["content"]).year >= 2026


async def test_discard_session_removes_receipts_and_blobs(env):
    body = (await call(env, "big", call_id="big-2")).json()
    await call(env, "small", call_id="small-2")
    await env["service"].discard_session(env["session_id"])
    async with env["sessionmaker"]() as session:
        assert await session.get(ActionResultBlob, body["resultRef"]) is None
        left = (await session.execute(select(ActionReceipt).where(ActionReceipt.session_id == env["session_id"]))).all()
    assert left == []
