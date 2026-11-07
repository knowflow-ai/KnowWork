"""宿主请业务服务激活对话（conversations/activation.py）：定时任务这类宿主插件自己发起一轮之前先来这里。
- 宿主令牌要对，插件种类要登记过，人要是空间成员，对话要在且归他；
- 宿主上已经没有这段对话时以恢复方式打开，带上对话的属主与新令牌；还开着的不重开（可能正跑着一轮）；
- 宿主打不开时回 503，宿主那边按官方的失败处理走。"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.activation import build_activation_router
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.pump import EventPump, PumpTimings
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.db.base import in_workspace, make_engine, make_workspace_sessionmaker
from knowwork_server.db.migrate import MAIN_LINE, upgrade_to_head
from knowwork_server.kernel.contract import KernelError, SessionBusy, SessionOwner

from .fakes import FakeKernel

pytestmark = pytest.mark.anyio
HOST_TOKEN = "h" * 40
MEMBERS = {("u1", "ws_a"), ("u2", "ws_a")}


@pytest.fixture
async def world(app_db):
    """和生产一样：应用账号不是超级用户、表开了行级安全，接口按宿主给的属主声明当前空间才查得到对话。"""
    await upgrade_to_head(app_db.owner_url, app_role=app_db.role, branches=MAIN_LINE)
    engine = make_engine(app_db.app_url)
    sessionmaker = make_workspace_sessionmaker(engine)
    kernel = FakeKernel()
    writer = BatchWriter(sessionmaker)
    broadcaster = Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster, timings=PumpTimings(idle_timeout=0.2))
    service = ConversationService(kernel, sessionmaker, pump, EventFeed(sessionmaker, writer, broadcaster,
                                                                         pump.produced_upto))
    app = FastAPI()
    app.include_router(build_activation_router(service, HOST_TOKEN, member=lambda user, ws: (user, ws) in MEMBERS))
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://internal")
    with in_workspace("ws_a"):
        conversation = await service.create(workspace_id="ws_a", user_id="u1")

    async def activate(body, token=HOST_TOKEN):
        return await http.post("/internal/host/v1/activations", json=body,
                               headers={"Authorization": f"Bearer {token}"})

    yield SimpleNamespace(kernel=kernel, conversation=conversation, activate=activate)
    await http.aclose()
    await pump.close()
    await engine.dispose()


def _body(conversation_id, user_id="u1", workspace_id="ws_a", kind="schedule"):
    return {"kind": kind, "sessionId": conversation_id, "owner": {"workspaceId": workspace_id, "userId": user_id}}


async def test_reopens_a_released_conversation_with_its_owner(world):
    kernel, conversation = world.kernel, world.conversation
    response = await world.activate(_body(conversation.id))
    assert response.status_code == 200, response.text
    reopened = kernel.opened[-1]
    assert (reopened.session_id, reopened.resume) == (conversation.id, True)
    assert reopened.owner == SessionOwner(workspace_id="ws_a", user_id="u1")


async def test_an_open_conversation_is_not_reopened(world):
    kernel, conversation = world.kernel, world.conversation
    kernel.open_effects = [SessionBusy("session_busy", "已经开着", 409)]
    response = await world.activate(_body(conversation.id))
    assert response.status_code == 200, response.text
    assert len(kernel.opened) == 1 and kernel.disposed == [], "开着的对话不重开，只有建对话时打开过一次"


async def test_refusals(world):
    conversation_id = world.conversation.id
    assert (await world.activate(_body(conversation_id), token="x" * 40)).status_code == 401
    assert (await world.activate(_body(conversation_id, kind="webhook"))).status_code == 422, "没登记的插件不放行"
    assert (await world.activate(_body(conversation_id, user_id="outsider"))).status_code == 403
    assert (await world.activate(_body(conversation_id, user_id="u2"))).status_code == 404, "对话不归这个人"
    assert (await world.activate(_body(conversation_id, workspace_id="ws_b"))).status_code == 403
    assert (await world.activate(_body("c_missing"))).status_code == 404
    assert (await world.activate({"kind": "schedule", "sessionId": conversation_id})).status_code == 422
    assert len(world.kernel.opened) == 1, "拒绝时不碰宿主"


async def test_host_failure_is_reported(world):
    world.kernel.open_effects = [KernelError("internal_error", "宿主出错了", 500)]
    response = await world.activate(_body(world.conversation.id))
    assert response.status_code == 503 and "internal_error" in response.json()["detail"]
