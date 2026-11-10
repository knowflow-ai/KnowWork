"""模型目录的进程内快照：TTL 内只读一次库、改了马上作废、加载途中被改的那份不算数；worker 用的目录不缓存；
别的进程（独立的模型网关）收到数据库通知就作废。"""

import asyncio

import pytest

from knowwork_server.db.base import make_held_engine
from knowwork_server.models import catalog_for_gateway, catalog_for_worker
from knowwork_server.models.cache import CatalogSnapshot, SnapshotCache
from knowwork_server.models.schemas import ModelCreate, ModelUpdate, ProviderCreate

from .conftest import MASTER_KEY

pytestmark = pytest.mark.anyio
EMPTY = CatalogSnapshot(models=(), providers={})


class Loader:
    def __init__(self) -> None:
        self.calls = 0
        self.gate: asyncio.Event | None = None

    async def __call__(self) -> CatalogSnapshot:
        self.calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return CatalogSnapshot(models=(), providers={str(self.calls): None})


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def test_reuses_snapshot_within_ttl_and_reloads_after():
    load, clock = Loader(), Clock()
    cache = SnapshotCache(load, ttl=10, clock=clock)
    first = await cache.get()
    clock.now = 9.9
    assert await cache.get() is first and load.calls == 1
    clock.now = 10.0
    assert await cache.get() is not first and load.calls == 2


async def test_invalidate_forces_reload():
    load = Loader()
    cache = SnapshotCache(load, ttl=60)
    await cache.get()
    cache.invalidate()
    await cache.get()
    assert load.calls == 2


async def test_snapshot_loaded_across_a_write_is_not_trusted():
    load = Loader()
    cache = SnapshotCache(load, ttl=60)
    load.gate = asyncio.Event()
    reading = asyncio.create_task(cache.get())
    await asyncio.sleep(0)
    cache.invalidate()  # 读的过程中有人改了：读到的可能是改之前的
    load.gate.set()
    await reading
    load.gate = None
    await cache.get()
    assert load.calls == 2


async def test_concurrent_readers_share_one_load():
    load = Loader()
    cache = SnapshotCache(load, ttl=60)
    load.gate = asyncio.Event()
    readers = [asyncio.create_task(cache.get()) for _ in range(20)]
    await asyncio.sleep(0)
    load.gate.set()
    results = await asyncio.gather(*readers)
    assert load.calls == 1 and all(r is results[0] for r in results)


async def test_zero_ttl_always_loads():
    load = Loader()
    cache = SnapshotCache(load, ttl=0)
    await cache.get()
    await cache.get()
    assert load.calls == 2


async def _configured(catalog):
    provider = await catalog.create_provider("root", ProviderCreate(name="P", kind="deepseek",
                                                                    base_url="https://api.example.com/v1", api_key="k"))
    model = await catalog.create_model("root", ModelCreate(provider_id=provider.id, alias="default", name="m-1"))
    return provider, model


async def test_catalog_reads_once_and_sees_its_own_writes(catalog, monkeypatch):
    _, model = await _configured(catalog)
    loads = 0
    original = catalog._load_snapshot

    async def counted():
        nonlocal loads
        loads += 1
        return await original()
    monkeypatch.setattr(catalog._snapshot, "_load", counted)
    for _ in range(3):
        assert (await catalog.resolve("ws1", "default")).upstream_model == "m-1"
    assert await catalog.default_capabilities("ws1") == frozenset({"chat"})
    assert await catalog.price_of("default") == (model.id, None, None)
    assert loads == 1
    await catalog.update_model("root", model.id, ModelUpdate(name="m-2"))
    assert (await catalog.resolve("ws1", "default")).upstream_model == "m-2"
    assert loads == 2


async def test_worker_catalog_sees_changes_made_elsewhere(catalog, sessionmaker):
    _, model = await _configured(catalog)
    worker = catalog_for_worker(sessionmaker, MASTER_KEY)
    assert (await worker.resolve_capable("ws1", "default", "chat")).upstream_model == "m-1"
    await catalog.update_model("root", model.id, ModelUpdate(name="m-2"))
    assert (await worker.resolve_capable("ws1", "default", "chat")).upstream_model == "m-2"


async def test_follower_invalidates_on_notification(catalog, sessionmaker, db):
    _, model = await _configured(catalog)
    reader = catalog_for_gateway(sessionmaker, MASTER_KEY)  # 快照 TTL 10 秒：下面换得过来只能靠通知
    assert (await reader.resolve("ws1", "default")).upstream_model == "m-1"
    held, stop, ready = make_held_engine(db.app_url), asyncio.Event(), asyncio.Event()
    follower = asyncio.create_task(reader.follow_changes(held, stop, ready=ready))
    await asyncio.wait_for(ready.wait(), 10)
    await catalog.update_model("root", model.id, ModelUpdate(name="m-2"))
    for _ in range(30):
        if (await reader.resolve("ws1", "default")).upstream_model == "m-2":
            break
        await asyncio.sleep(0.1)
    else:
        raise AssertionError("收到通知后快照没有作废")
    stop.set()
    await follower
    await held.dispose()


async def test_show_thinking_defaults_on_and_follows_updates(catalog):
    _, model = await _configured(catalog)
    assert model.show_thinking is True
    assert await catalog.shows_thinking("default") is True
    await catalog.update_model("root", model.id, ModelUpdate(show_thinking=False))
    assert await catalog.shows_thinking("default") is False, "改了马上生效（快照作废）"
    assert await catalog.shows_thinking(None) is False, "没带别名按 default 找"
    assert await catalog.shows_thinking("no-such-model") is True, "找不到的照常显示"
