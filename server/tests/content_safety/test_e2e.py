"""端到端：业务服务存策略，宿主（真实 DSH + 官方模拟模型 + KnowWork 宿主插件）把文字送回业务服务的检查接口，命中就拦下。

一段会话两轮：
1. 员工的消息带敏感词：不调模型，这一轮以 blocked 结束；投影出来提问照常显示、这一轮失败、话术是管理员写的；
2. 消息干净、模型的回答带敏感词：回答收掉，以 content_blocked 结束。
两次都写一条审计（不记原文）。
"""

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.access.service import AccessService
from knowwork_server.content_safety import build_content_safety, internal_router
from knowwork_server.content_safety.policy import draft_of
from knowwork_server.conversations.events_v1 import ProjectionState, project
from knowwork_server.db.base import Base, make_engine, make_sessionmaker
from knowwork_server.kernel.contract import LIVE_SOURCE, TurnInput
from knowwork_server.kernel.host_client import HostKernel
from knowwork_server.tools import build_tool_service
from knowwork_server.tools.api import build_internal_router as tools_internal_router
from knowwork_server.tools.registry import KW_NOW, StaticToolRegistry

from ..support import HostProcess, Served, free_port
from ..support_mock_llm import OVERLAY, MockLlmProcess

pytestmark = pytest.mark.anyio
SESSION = "conv-content-safety-e2e"
WORD = "星河计划"
MESSAGE = "当前对话内容违反公司规范，请修改后重新输入"


class Sink:
    def __init__(self) -> None:
        self.events = []

    async def record(self, event) -> None:
        self.events.append(event)


def positions_after(frames: list) -> dict[str, int]:
    """上一轮读到哪：每个来源最后一帧的序号（宿主订阅从这之后回放，同 pump 记的续读位置）。
    逐字输出的临时帧（来源 live）不推进续读位置，不算。"""
    found: dict[str, int] = {}
    for frame in frames:
        if frame.epoch != LIVE_SOURCE:
            found[frame.epoch] = max(frame.cursor, found.get(frame.epoch, -1))
    return found


async def one_turn(kernel: HostKernel, text: str, positions: dict[str, int] | None = None) -> list:
    """发一条消息、等这一轮结束；positions 是上一轮读到的位置（positions_after），第一轮不给。"""
    await kernel.send(SESSION, TurnInput(text=text))
    frames = []
    async for frame in kernel.subscribe(SESSION, positions=positions):
        frames.append(frame)
        if frame.kind == "session/event" and (frame.data or {}).get("type") == "turn/end":
            return frames
    return frames


def projected(frames: list) -> list[dict]:
    state, out = ProjectionState(), []
    for seq, frame in enumerate(frames, start=1):
        state, events = project(state, frame.kind, frame.data, seq=seq)
        out.extend(events)
    return out


async def test_input_and_output_are_checked_against_the_workspace_policy(database_url):
    engine = make_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessionmaker = make_sessionmaker(engine)
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id="ws-cs-e2e", name="安全", owner_user_id="boss")
    await access.add_member("boss", "ws-cs-e2e", "u1", "editor")
    sink = Sink()
    safety = build_content_safety(sessionmaker, access, sink)
    await safety.service.save("boss", "ws-cs-e2e", version=0, draft=draft_of(
        input_enabled=True, output_enabled=True, words=WORD, block_message=MESSAGE))
    tools = build_tool_service("k" * 40, sessionmaker, registry=StaticToolRegistry.of([KW_NOW]))

    def member(user_id: str, workspace_id: str) -> bool:
        return access.check(user_id, workspace_id, "workspace", "use")

    internal = FastAPI()
    internal.include_router(tools_internal_router(tools))
    internal.include_router(internal_router(safety, tools, member))
    port = free_port()
    mock = MockLlmProcess(sequence=["success"], successText=f"根据资料，{WORD}本季度完成了八成")
    try:
        async with mock, Served(internal, port):
            env = {**mock.host_env(),
                   "KNOWWORK_TOOLS_BASE_URL": f"http://127.0.0.1:{port}/internal/extensions/tools",
                   "KNOWWORK_CONTENT_SAFETY_BASE_URL": f"http://127.0.0.1:{port}/internal/extensions/content-safety"}
            async with HostProcess(model_base_url="http://127.0.0.1:1/unused", model_name="deepseek-chat",
                                   patch=OVERLAY, extra_env=env) as host:
                base = f"http://127.0.0.1:{host.port}"
                kernel = HostKernel(base, host.token)
                try:
                    token = await tools.issue_session_token("ws-cs-e2e", SESSION, user_id="u1")
                    async with httpx.AsyncClient(base_url=base,
                                                 headers={"authorization": f"Bearer {host.token}"}) as client:
                        opened = await client.post("/v1/sessions", json={"sessionId": SESSION,
                                                                         "tools": {"token": token}})
                        assert opened.status_code == 201, opened.text
                    first = await asyncio.wait_for(one_turn(kernel, f"把{WORD}的进度整理一下"), timeout=30)
                    second = await asyncio.wait_for(
                        one_turn(kernel, "整理一下本季度的进度", positions_after(first)), timeout=30)
                finally:
                    await kernel.aclose()
            requests = await mock.requests()

        events = projected(first)
        asked = [e for e in events if e["type"] == "item.completed" and e["data"].get("role") == "user"]
        assert [e["data"]["text"] for e in asked] == [f"把{WORD}的进度整理一下"], "被拦下的提问照常显示"
        assert events[-1]["type"] == "run.failed"
        assert events[-1]["data"] == {"code": "content_blocked", "message": MESSAGE}

        ended = projected(second)[-1]
        assert ended["data"] == {"code": "content_blocked", "message": MESSAGE}
        assert WORD not in json.dumps([f.data for f in second], ensure_ascii=False), "回答里的词不推给客户端"

        assert len(requests) == 1, "第一轮命中没有调模型"
        assert WORD not in json.dumps(requests[0]["body"]["messages"], ensure_ascii=False), \
            "第二轮发给模型的历史里没有被拦下的那句"
        directions = [event.normalized().detail["direction"] for event in sink.events]
        assert directions == ["input", "output"]
        assert all(event.actor_id == "u1" and event.resource_id == SESSION for event in sink.events)
    finally:
        await engine.dispose()
