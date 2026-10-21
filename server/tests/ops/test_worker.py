"""worker 占位入口：配置可加载，收到停止信号即退出；配置不完整立即失败。"""

import asyncio

import pytest
from pydantic import ValidationError

from knowwork_server.config import get_settings
from knowwork_server.ops import worker

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_worker_stops_on_event(monkeypatch):
    monkeypatch.setenv("KNOWWORK_MASTER_KEY", "k" * 40)
    stop = asyncio.Event()
    task = asyncio.create_task(worker.run(stop))
    await asyncio.sleep(0.05)
    assert not task.done()
    stop.set()
    await asyncio.wait_for(task, 5)


async def test_worker_fails_fast_without_master_key(monkeypatch):
    monkeypatch.delenv("KNOWWORK_MASTER_KEY", raising=False)
    monkeypatch.delenv("KNOWWORK_MASTER_KEY_FILE", raising=False)
    with pytest.raises(ValidationError):
        await worker.run(asyncio.Event())
