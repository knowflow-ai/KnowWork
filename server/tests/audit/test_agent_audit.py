"""Agent 的操作：工具执行经 ToolService 的审计回调记成 actor_type=agent，带工具名、风险、结果与回执 id。"""

import logging

import pytest

from knowwork_server.audit.agent import tool_audit_hook
from knowwork_server.db.base import make_workspace_sessionmaker
from knowwork_server.tools import build_tool_service
from knowwork_server.tools.registry import KW_NOW, BuiltinTool, StaticToolRegistry, ToolFailure, ToolSpec
from knowwork_server.tools.service import ExecuteRequest, ToolAccessError

pytestmark = pytest.mark.anyio
SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"], "additionalProperties": False}


async def note(_ctx, arguments):
    if arguments["q"] == "refuse":
        raise ToolFailure("台账已锁定")
    return "已记录"


@pytest.fixture
async def tools(app_sessionmaker, memory_sink):
    scoped = make_workspace_sessionmaker(app_sessionmaker.kw["bind"])
    registry = StaticToolRegistry.of([KW_NOW, BuiltinTool(ToolSpec("kw_note", "记一笔", SCHEMA, "write"), note)])
    service = build_tool_service("m" * 40, scoped, registry=registry, audit=tool_audit_hook(memory_sink))
    claims = service.verify(await service.issue_session_token("ws1", "conv-1", user_id="alice"))
    return service, claims


def request(q, call="c1", tool="kw_note"):
    return ExecuteRequest(tool_name=tool, arguments={"q": q}, agent_session_id="agent-1", call_key=f"agent-1:{call}")


async def test_success_failure_and_replay(tools, memory_sink):
    service, claims = tools
    first = await service.execute(claims, request("付款 30 天"))
    replay = await service.execute(claims, request("付款 30 天"))
    await service.execute(claims, request("refuse", call="c2"))
    assert replay.replayed
    ok, refused = memory_sink.events  # 重放不重复记
    assert (ok.actor_type, ok.actor_id, ok.credential_type, ok.session_id) == ("agent", "alice", "tool_token", "conv-1")
    assert (ok.action, ok.resource_type, ok.resource_id, ok.result) == ("tool.execute", "tool", "kw_note", "success")
    assert ok.detail["risk"] == "write" and ok.detail["actionId"] == first.action_id
    assert ok.detail["agentSessionId"] == "agent-1" and len(ok.detail["argsDigest"]) == 64
    assert "付款" not in str(ok.detail)  # 参数原文不进审计
    assert refused.result == "failed" and refused.detail["actionId"]


async def test_denied_and_rejected_calls(tools, memory_sink):
    service, claims = tools
    with pytest.raises(ToolAccessError):
        await service.execute(claims, request("x", tool="kw_secret"))
    with pytest.raises(ToolAccessError):
        await service.execute(claims, ExecuteRequest(tool_name="kw_note", arguments={"bad": 1},
                                                     agent_session_id="agent-1", call_key="agent-1:c9"))
    denied, invalid = memory_sink.events
    assert (denied.result, denied.detail["code"], denied.resource_id) == ("denied", "tool_not_available", "kw_secret")
    assert "risk" not in denied.detail
    assert (invalid.result, invalid.detail["code"], invalid.detail["risk"]) == ("failed", "invalid_arguments", "write")


async def test_hook_failure_does_not_break_the_call(app_sessionmaker, caplog):
    async def broken(_execution):
        raise RuntimeError("审计挂了")

    scoped = make_workspace_sessionmaker(app_sessionmaker.kw["bind"])
    service = build_tool_service("m" * 40, scoped, registry=StaticToolRegistry.of([KW_NOW]), audit=broken)
    claims = service.verify(await service.issue_session_token("ws1", "conv-2", user_id="alice"))
    with caplog.at_level(logging.ERROR):
        result = await service.execute(claims, ExecuteRequest(tool_name="kw_now", arguments={},
                                                              agent_session_id="a", call_key="a:1"))
    assert result.status == "succeeded" and "tool audit hook failed" in caplog.text


async def test_unexpected_errors_are_recorded_then_raised(tools, memory_sink, monkeypatch):
    service, claims = tools

    async def explode(_call):
        raise OSError("db gone")

    monkeypatch.setattr(service._receipts, "claim", explode)
    with pytest.raises(OSError):
        await service.execute(claims, request("x", call="c5"))
    [event] = memory_sink.events
    assert (event.result, event.detail["code"]) == ("failed", "internal_error")
