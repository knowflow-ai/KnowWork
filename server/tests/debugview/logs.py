"""测试用的会话日志：一轮完整的（失败重试、工具调用、子 Agent、审批、技能），后面接着一轮还没跑完的。"""

from datetime import UTC, datetime
from decimal import Decimal

from knowwork_server.conversations.runlog import RawEvent
from knowwork_server.models.recorder import StepUsage

AT = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def frame(seq, kind, data, time=None):
    return RawEvent(seq=seq, kind="session/event",
                    data={"seq": seq, "type": kind, "time": time if time is not None else 1000 + seq, "data": data},
                    at=AT)


def child(seq, kind, data, time=None):
    return RawEvent(seq=seq, kind="subagent/event",
                    data={"sessionId": "kid", "event": {"seq": seq, "type": kind, "time": time or 1000 + seq,
                                                        "data": data}}, at=AT)


def host(seq, kind, data):
    return RawEvent(seq=seq, kind=kind, data=data, at=AT)


def text(role, value, source=None):
    message = {"role": role, "content": [{"type": "text", "text": value}]}
    return {**message, "source": {"kind": source}} if source else message


LOG = (
    frame(0, "agent/inbox/spliced", {"target": "next-turn", "inserted": [text("user", "你好", "user")]}),
    frame(1, "turn/start", {"turn": 1}),
    frame(2, "request/header", {"startsSeries": True, "reason": "start",
                                "header": {"tools": [{"name": "bash", "description": "执行命令"}], "config": {}}}),
    frame(3, "system/message", {"turn": 1, "step": 1, "message": text("system", "你是助手")}),
    frame(4, "user/message", text("user", "当前时间", "runtime-context")),
    frame(5, "step/start", {"turn": 1, "step": 1}, time=2000),
    frame(6, "assistant/attempt", {"turn": 1, "step": 1, "stream": [
        {"type": "chunk", "chunk": {"type": "finish", "reason": {"kind": "error", "failure": {"message": "504 超时"}}}}]}),
    frame(7, "llm/retry", {"turn": 1, "step": 1, "retry": 1, "failure": {"code": "SERVER", "message": "504 超时"}}),
    frame(8, "assistant/message", {"turn": 1, "step": 1, "message": {"role": "assistant", "content": [
        {"type": "reasoning", "text": "先看看目录"}, {"type": "text", "text": "我来查"},
        {"type": "tool-call", "id": "call1", "name": "bash", "arguments": '{"cmd":"ls"}'}]}}),
    frame(9, "tool/call", {"turn": 1, "step": 1, "callId": "call1", "name": "bash", "arguments": '{"cmd":"ls"}'},
          time=2100),
    frame(10, "tool/result", {"turn": 1, "step": 1, "message": {
        "role": "tool", "toolCallId": "call1", "isError": False, "content": [{"type": "text", "text": "a.txt"}]}},
          time=2400),
    frame(11, "step/end", {"turn": 1, "step": 1}, time=2500),
    frame(12, "step/start", {"turn": 1, "step": 2}, time=2600),
    frame(13, "subagent/catalog", {"childId": "kid", "label": "子任务"}),
    child(14, "step/start", {"turn": 1, "step": 1}),
    child(15, "tool/call", {"turn": 1, "step": 1, "callId": "k1", "name": "read", "arguments": "{}"}),
    child(16, "step/end", {"turn": 1, "step": 1}),
    frame(17, "approval/asked", {"id": "a1", "toolName": "write", "callId": "call2"}),
    frame(18, "approval/decided", {"id": "a1", "outcome": "allowed-session"}),
    host(19, "skill/loaded", {"skill": "report", "version": "1.0.0", "sessionId": "c1"}),
    frame(20, "assistant/message", {"turn": 1, "step": 2, "message": {"role": "assistant", "content": [
        {"type": "text", "text": "完成"}]}}),
    frame(21, "step/end", {"turn": 1, "step": 2}, time=2900),
    frame(22, "turn/end", {"turn": 1, "reason": {"kind": "completed"}}, time=3000),
    frame(23, "user/message", text("user", "再来一次", "user")),
    frame(24, "turn/start", {"turn": 2}),
    frame(25, "step/start", {"turn": 2, "step": 3}),
)

USAGE = (StepUsage(turn=1, step=1, model_alias="default", input_tokens=100, output_tokens=20,
                   cost=Decimal("0.00120"), duration_ms=900, status="ok"),
         StepUsage(turn=1, step=2, model_alias="default", input_tokens=150, output_tokens=5, cost=None,
                   duration_ms=300, status="ok"))
