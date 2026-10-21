"""会话列表、改名与删除：只看得到自己的；删除会释放宿主会话、停泵、清掉事件与关联数据，且之后再也访问不到。"""

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from knowwork_server.conversations.api import build_router
from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.models import RunEvent
from knowwork_server.conversations.pump import EventPump
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.kernel.contract import SessionNotFound

from .fakes import FakeKernel, session_event, token_actor

pytestmark = pytest.mark.anyio

ALICE, BOB = "a" * 48, "b" * 48


def stack(kernel, sessionmaker, erased):
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    writer.start()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    feed = EventFeed(sessionmaker, writer, broadcaster, pump.produced_upto)

    async def eraser(conversation):
        erased.append(conversation.id)

    service = ConversationService(kernel, sessionmaker, pump, feed, erasers=(eraser,))
    app = FastAPI()
    app.include_router(build_router(service, token_actor(ALICE, user_id="alice")), prefix="")
    other = FastAPI()
    other.include_router(build_router(service, token_actor(BOB, user_id="bob")))
    alice = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw",
                              headers={"authorization": f"Bearer {ALICE}"})
    bob = httpx.AsyncClient(transport=httpx.ASGITransport(app=other), base_url="http://kw",
                            headers={"authorization": f"Bearer {BOB}"})
    return alice, bob, pump, writer


async def test_list_rename_and_isolation(sessionmaker):
    kernel, erased = FakeKernel(), []
    alice, bob, pump, writer = stack(kernel, sessionmaker, erased)
    async with alice, bob:
        first = (await alice.post("/api/conversations", json={"title": "第一个"})).json()
        second = (await alice.post("/api/conversations", json={"title": "第二个"})).json()
        await bob.post("/api/conversations", json={"title": "别人的"})
        await alice.post(f"/api/conversations/{first['id']}/messages", json={"text": "hi"})

        listed = (await alice.get("/api/conversations")).json()
        assert [c["id"] for c in listed["items"]] == [first["id"], second["id"]], "最近有动静的排前面"
        assert all(c["title"] != "别人的" for c in listed["items"])

        page = (await alice.get("/api/conversations", params={"limit": 1})).json()
        assert len(page["items"]) == 1 and page["next"]
        rest = (await alice.get("/api/conversations", params={"limit": 1, "before": page["next"]})).json()
        assert [c["id"] for c in rest["items"]] == [second["id"]]

        renamed = await alice.patch(f"/api/conversations/{second['id']}", json={"title": "  改过的名字  "})
        assert renamed.status_code == 200 and renamed.json()["title"] == "改过的名字"
        assert (await bob.patch(f"/api/conversations/{second['id']}", json={"title": "x"})).status_code == 404
        assert (await alice.get("/api/conversations", params={"limit": 0})).status_code == 422
    await pump.close()
    await writer.close()


async def test_delete_releases_host_session_and_erases_everything(sessionmaker):
    kernel, erased = FakeKernel([[session_event(1, "turn/start", {"turn": 1})]]), []
    alice, bob, pump, writer = stack(kernel, sessionmaker, erased)
    async with alice, bob:
        cid = (await alice.post("/api/conversations", json={})).json()["id"]
        await alice.post(f"/api/conversations/{cid}/messages", json={"text": "hi"})
        assert (await bob.delete(f"/api/conversations/{cid}")).status_code == 404, "不能删别人的"

        kernel.dispose_effects = [SessionNotFound("session_not_found", "gone", 404)]  # 宿主上已不在也照删
        assert (await alice.delete(f"/api/conversations/{cid}")).status_code == 204
        assert kernel.disposed == [cid]
        assert erased == [cid]
        assert not pump.is_active(cid)
        await writer.flush()
        async with sessionmaker() as session:
            left = await session.scalar(select(func.count()).select_from(RunEvent).where(RunEvent.conversation_id == cid))
        assert left == 0
        assert (await alice.get(f"/api/conversations/{cid}/events")).status_code == 404
        assert (await alice.delete(f"/api/conversations/{cid}")).status_code == 404
    await pump.close()
    await writer.close()
