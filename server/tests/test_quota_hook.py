"""用量配额的判定口子（conversations/quota.py）：各端开始一轮之前问扩展登记的判定，超了这一轮不开始、各处同一句话。
- 没登记时什么都不调（开源版不限制）；
- 云端发消息、桌面端开始一轮、定时任务到点激活都先判，判出超了不送给宿主；
- 接口回 429 与 {code: quota_exceeded, message, scope, resets_on}，前端照常显示 message。"""

from datetime import date

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.activation import build_activation_router
from knowwork_server.conversations.api import build_router
from knowwork_server.conversations.device import DeviceConversations
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.pump import EventPump
from knowwork_server.conversations.quota import QuotaExceeded, QuotaPolicies, TurnStart
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.kernel.contract import TurnInput

from .fakes import FakeKernel, token_actor

pytestmark = pytest.mark.anyio

ALICE = "a" * 48
HOST_TOKEN = "h" * 40
OWNER = "quota-owner"
USED_UP = "你本月的额度已用完，12 月 1 日恢复。需要更多请联系空间管理员。"


class Gate:
    """扩展登记的判定替身：记下每次问的是哪一轮，closed 时说额度用完。"""

    def __init__(self) -> None:
        self.asked: list[TurnStart] = []
        self.closed = False

    async def __call__(self, start: TurnStart) -> None:
        self.asked.append(start)
        if self.closed:
            raise QuotaExceeded(USED_UP, scope="personal", resets_on=date(2026, 12, 1))


@pytest.fixture
async def world(sessionmaker):
    kernel = FakeKernel()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    writer.start()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    feed = EventFeed(sessionmaker, writer, broadcaster, pump.produced_upto)
    policies, gate = QuotaPolicies(), Gate()
    policies.set(gate)
    service = ConversationService(kernel, sessionmaker, pump, feed, quota=policies)
    yield kernel, service, gate
    await pump.close()
    await writer.close()


def test_only_one_policy_can_be_registered():
    policies = QuotaPolicies()
    assert not policies
    policies.set(Gate())
    assert policies
    with pytest.raises(ValueError):
        policies.set(Gate())


async def test_without_a_policy_nothing_is_asked(sessionmaker):
    kernel = FakeKernel()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    writer.start()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    service = ConversationService(kernel, sessionmaker, pump, EventFeed(sessionmaker, writer, broadcaster,
                                                                         pump.produced_upto))
    try:
        conversation = await service.create(workspace_id="default", user_id=OWNER)
        await service.send(conversation, TurnInput(text="你好"))
        assert len(kernel.sent) == 1
    finally:
        await pump.close()
        await writer.close()


async def test_every_turn_start_asks_with_who_and_how(world):
    kernel, service, gate = world
    conversation = await service.create(workspace_id="default", user_id=OWNER)
    await service.send(conversation, TurnInput(text="你好"))
    await service.activate(conversation)
    device = DeviceConversations(service, service._pump)
    local = await device.create(workspace_id="default", user_id=OWNER)
    await device.prepare_turn(local, TurnInput(text="本机"))
    assert gate.asked == [
        TurnStart(workspace_id="default", user_id=OWNER, conversation_id=conversation.id, trigger="user"),
        TurnStart(workspace_id="default", user_id=OWNER, conversation_id=conversation.id, trigger="schedule"),
        TurnStart(workspace_id="default", user_id=OWNER, conversation_id=local.id, trigger="user"),
    ]
    assert len(kernel.sent) == 1


async def test_used_up_stops_the_turn_before_the_host_sees_it(world):
    kernel, service, gate = world
    conversation = await service.create(workspace_id="default", user_id=OWNER)
    opened = len(kernel.opened)
    gate.closed = True
    with pytest.raises(QuotaExceeded):
        await service.send(conversation, TurnInput(text="你好"))
    with pytest.raises(QuotaExceeded):
        await service.activate(conversation)
    device = DeviceConversations(service, service._pump)
    local = await device.create(workspace_id="default", user_id=OWNER)
    with pytest.raises(QuotaExceeded):
        await device.prepare_turn(local, TurnInput(text="本机"))
    assert kernel.sent == [] and len(kernel.opened) == opened, "没送、也没为它重开宿主会话"


class RecordingAttachments:
    """附件来源的替身：记下哪些附件被认领进了对话。"""

    def __init__(self) -> None:
        self.claimed: list[tuple[str, ...]] = []

    async def for_turn(self, conversation, ids):
        self.claimed.append(tuple(ids))
        return ()


async def test_a_blocked_turn_claims_no_attachments(sessionmaker):
    kernel, attachments, gate = FakeKernel(), RecordingAttachments(), Gate()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    writer.start()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    policies = QuotaPolicies()
    policies.set(gate)
    service = ConversationService(kernel, sessionmaker, pump, EventFeed(sessionmaker, writer, broadcaster,
                                                                         pump.produced_upto),
                                  quota=policies, attachments=attachments)
    try:
        conversation = await service.create(workspace_id="default", user_id=OWNER)
        gate.closed = True
        with pytest.raises(QuotaExceeded):
            await service.send(conversation, TurnInput(text="看看这份"), attachment_ids=("att_1",))
        assert attachments.claimed == [], "额度用完的一轮不占附件：用户换个时候再发，附件还是他输入框里的"
        gate.closed = False
        await service.send(conversation, TurnInput(text="看看这份"), attachment_ids=("att_1",))
        assert attachments.claimed == [("att_1",)]
    finally:
        await pump.close()
        await writer.close()


async def test_the_api_says_why_and_when_it_comes_back(world):
    _, service, gate = world
    app = FastAPI()
    app.include_router(build_router(service, token_actor(ALICE, user_id=OWNER)))
    app.include_router(build_activation_router(service, HOST_TOKEN, member=lambda user, workspace: True))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as http:
        conversation = await service.create(workspace_id="default", user_id=OWNER)
        gate.closed = True
        sent = await http.post(f"/api/workspaces/default/conversations/{conversation.id}/messages",
                               json={"text": "你好"}, headers={"authorization": f"Bearer {ALICE}"})
        activated = await http.post("/internal/host/v1/activations", headers={"Authorization": f"Bearer {HOST_TOKEN}"},
                                    json={"kind": "schedule", "sessionId": conversation.id,
                                          "owner": {"workspaceId": "default", "userId": OWNER}})
    expected = {"code": "quota_exceeded", "message": USED_UP, "scope": "personal", "resets_on": "2026-12-01"}
    assert (sent.status_code, sent.json()["detail"]) == (429, expected)
    assert (activated.status_code, activated.json()["detail"]) == (429, expected)
