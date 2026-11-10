"""用量记录找对话属主：查到的记住不再查库，查不到的不记；记的个数有上限。入口用的事件循环装了 uvloop 就用它。"""

import asyncio
from types import SimpleNamespace

import pytest
import uvloop

from knowwork_server import app
from knowwork_server.ops.eventloop import loop_factory

pytestmark = pytest.mark.anyio


class Sessions:
    def __init__(self, owners: dict[str, str]) -> None:
        self.owners = owners
        self.reads = 0

    def __call__(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, conversation_id):
        self.reads += 1
        user_id = self.owners.get(conversation_id)
        return None if user_id is None else SimpleNamespace(user_id=user_id)


async def test_owner_is_read_once_and_missing_is_not_remembered():
    sessions = Sessions({"c1": "alice"})
    owner_of = app._owner_lookup(sessions)
    assert [await owner_of("c1") for _ in range(3)] == ["alice"] * 3
    assert sessions.reads == 1
    assert await owner_of("c2") is None
    sessions.owners["c2"] = "bob"  # 对话随后建好了
    assert await owner_of("c2") == "bob"


async def test_remembers_at_most_the_configured_number(monkeypatch):
    monkeypatch.setattr(app, "OWNER_CACHE_SIZE", 2)
    sessions = Sessions({"c1": "a", "c2": "b", "c3": "c"})
    owner_of = app._owner_lookup(sessions)
    for cid in ("c1", "c2", "c1", "c3"):  # c2 最久没用，被挤掉
        await owner_of(cid)
    reads = sessions.reads
    await owner_of("c1")
    await owner_of("c3")
    assert sessions.reads == reads
    await owner_of("c2")
    assert sessions.reads == reads + 1


def test_entry_points_run_on_uvloop():
    factory = loop_factory()
    assert factory is uvloop.new_event_loop

    async def kind():
        return type(asyncio.get_running_loop()).__module__

    assert asyncio.run(kind(), loop_factory=factory).startswith("uvloop")
