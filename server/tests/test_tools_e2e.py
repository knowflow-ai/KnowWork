"""端到端：宿主（真实 DSH + 官方模拟模型）经工具桥回调业务服务内部工具接口。

模拟模型两次发出同一个工具调用（官方模拟服务的调用 ID 固定为 mock-call-1），验证：
工具描述进入模型请求、工具结果回到下一次模型请求、动作回执落库、同一调用不重复执行。
"""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

import knowwork_server.tools.models  # noqa: F401  注册表结构
from knowwork_server.db.base import Base, make_engine, make_sessionmaker
from knowwork_server.kernel.contract import TurnInput
from knowwork_server.kernel.host_client import HostKernel
from knowwork_server.tools import build_tool_service
from knowwork_server.tools.api import build_internal_router
from knowwork_server.tools.models import ActionReceipt
from knowwork_server.tools.registry import KW_NOW, BuiltinTool, StaticToolRegistry, ToolSpec

from .support import HostProcess, Served, free_port
from .support_mock_llm import OVERLAY, MockLlmProcess

pytestmark = pytest.mark.anyio
SESSION = "conv-tools-e2e"


def counter_tool(calls: list[dict]) -> BuiltinTool:
    async def handler(_ctx, arguments):
        calls.append(arguments)
        return f"台账「{arguments['q']}」计数={len(calls)}"

    schema = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    return BuiltinTool(ToolSpec("kw_counter", "给台账计数", schema, "read"), handler)


async def until_turn_end(kernel: HostKernel) -> list:
    frames = []
    async for frame in kernel.subscribe(SESSION):
        frames.append(frame)
        if frame.kind == "session/event" and (frame.data or {}).get("type") == "turn/end":
            return frames
    return frames


async def test_bridge_tool_round_trip_with_receipt_and_dedup(database_url):
    engine = make_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)
    calls: list[dict] = []
    service = build_tool_service("k" * 40, sessionmaker,
                                 registry=StaticToolRegistry.of([KW_NOW, counter_tool(calls)]))
    internal = FastAPI()
    internal.include_router(build_internal_router(service))
    internal_port = free_port()

    mock = MockLlmProcess(sequence=["tool_call_success", "tool_call_success", "success"], toolName="kw_counter",
                          toolArguments=json.dumps({"q": "采购"}), successText="已经统计完毕")
    async with mock, Served(internal, internal_port):
        tools_url = f"http://127.0.0.1:{internal_port}/internal/extensions/tools"
        async with HostProcess(model_base_url="http://127.0.0.1:1/unused", model_name="deepseek-chat", patch=OVERLAY,
                               extra_env={**mock.host_env(), "KNOWWORK_TOOLS_BASE_URL": tools_url}) as host:
            base = f"http://127.0.0.1:{host.port}"
            kernel = HostKernel(base, host.token)
            try:
                token = await service.issue_session_token("ws1", SESSION, user_id="u1")
                async with httpx.AsyncClient(base_url=base, headers={"authorization": f"Bearer {host.token}"}) as client:
                    opened = await client.post("/v1/sessions", json={"sessionId": SESSION, "tools": {"token": token}})
                    assert opened.status_code == 201, opened.text
                await kernel.send(SESSION, TurnInput(text="统计采购台账"))
                frames = await asyncio.wait_for(until_turn_end(kernel), timeout=30)
            finally:
                await kernel.aclose()

        requests = await mock.requests()
    try:
        end = next(f for f in frames if f.kind == "session/event" and f.data.get("type") == "turn/end")
        assert end.data["data"]["reason"]["kind"] == "completed"
        assert "已经统计完毕" in json.dumps([f.data for f in frames], ensure_ascii=False)

        assert len(requests) == 3
        tool_names = {t["name"] for t in requests[0]["body"]["tools"]}
        assert {"kw_counter", "kw_now"} <= tool_names, tool_names
        second = json.dumps(requests[1]["body"]["messages"], ensure_ascii=False)
        assert "台账「采购」计数=1" in second, "工具结果没有回到下一次模型请求"
        third = json.dumps(requests[2]["body"]["messages"], ensure_ascii=False)
        assert "计数=2" not in third, "同一调用被执行了两次"
        assert calls == [{"q": "采购"}]

        async with sessionmaker() as session:
            receipts = (await session.execute(select(ActionReceipt).where(ActionReceipt.session_id == SESSION))).scalars().all()
        assert len(receipts) == 1
        assert receipts[0].status == "succeeded"
        assert receipts[0].tool_name == "kw_counter"
        assert receipts[0].idempotency_key == f"{SESSION}|{SESSION}:mock-call-1"
    finally:
        await engine.dispose()
