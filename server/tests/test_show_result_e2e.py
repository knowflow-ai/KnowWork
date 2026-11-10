"""端到端：真实宿主（DSH + 官方模拟模型）注册 show_result、模型交 tables 调用它，卡片（表 + 怎么画 + 出处）落在工具结果的
事件上，给模型的只有展示了什么；使用规则进了系统提示；没有结果来源时模型看不到 query_ids。"""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

import knowwork_server.tools.models  # noqa: F401  注册表结构
from knowwork_server.db.base import Base, make_engine, make_sessionmaker
from knowwork_server.kernel.contract import TurnInput
from knowwork_server.kernel.host_client import HostKernel
from knowwork_server.tools import build_tool_service
from knowwork_server.tools.api import build_internal_router
from knowwork_server.tools.registry import KW_NOW, StaticToolRegistry
from knowwork_server.tools.results import ResultSources
from knowwork_server.tools.show_result import build_show_result

from .support import HostProcess, Served, free_port
from .support_mock_llm import OVERLAY, MockLlmProcess

pytestmark = pytest.mark.anyio
SESSION = "conv-show-result-e2e"
TABLE = {"columns": [{"name": "月份", "kind": "time"}, {"name": "销售额", "kind": "number", "unit": "万元"}],
             "rows": [["2026-01", 120.5], ["2026-02", 98]], "source": "附件 2025年销售.xlsx，按月求和"}
ARGUMENTS = {"tables": [TABLE]}


async def until_turn_end(kernel: HostKernel) -> list:
    frames = []
    async for frame in kernel.subscribe(SESSION):
        frames.append(frame)
        if frame.kind == "session/event" and (frame.data or {}).get("type") == "turn/end":
            return frames
    return frames


async def test_agent_shows_a_chart_card_through_the_real_host(database_url):
    engine = make_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    service = build_tool_service("k" * 40, make_sessionmaker(engine),
                                 registry=StaticToolRegistry.of([KW_NOW, build_show_result(ResultSources())]))
    internal = FastAPI()
    internal.include_router(build_internal_router(service))
    internal_port = free_port()

    mock = MockLlmProcess(sequence=["tool_call_success", "success"], toolName="show_result",
                          toolArguments=json.dumps(ARGUMENTS, ensure_ascii=False), successText="一月比二月高")
    try:
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
                    await kernel.send(SESSION, TurnInput(text="按月画一下销售额"))
                    frames = await asyncio.wait_for(until_turn_end(kernel), timeout=30)
                finally:
                    await kernel.aclose()
            requests = await mock.requests()
    finally:
        await engine.dispose()

    end = next(f for f in frames if f.kind == "session/event" and f.data.get("type") == "turn/end")
    assert end.data["data"]["reason"]["kind"] == "completed"
    first = requests[0]["body"]
    described = next(t for t in first["tools"] if t["name"] == "show_result")
    assert "query_ids" not in json.dumps(described), "没有结果来源时不给 query_ids"
    assert "不要在沙箱里用 Python 画图片" in json.dumps(first, ensure_ascii=False), "使用规则没进系统提示"
    events = json.dumps([f.data for f in frames], ensure_ascii=False)
    assert '"chart": {"type": "line", "x": "月份", "y": ["销售额"]}' in events, "卡片没落到工具结果的事件上"
    assert "销售额 · 按月份" in events and '"label": "来源"' in events
    second = json.dumps(requests[1]["body"]["messages"], ensure_ascii=False)
    assert "销售额 · 按月份" in second and "折线图" in second
    assert second.count("120.5") == 1, "数只在模型自己的调用参数里，工具结果不再给一遍"
