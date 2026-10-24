"""LISTEN 任务通知：独占一条数据库连接，收到 NOTIFY 就唤醒对应的执行槽。

连接断了隔几秒重连；断开期间执行槽靠轮询兜底，任务不会漏，只是晚几秒被领取。
"""

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncEngine

from .queue import CHANNEL
from .runner import wait_any

log = logging.getLogger(__name__)


async def listen(engine: AsyncEngine, on_message: Callable[[str], None], stop: asyncio.Event, *,
                 retry_seconds: float = 5.0, ready: asyncio.Event | None = None) -> None:
    def deliver(_connection, _pid, _channel, payload: str) -> None:
        on_message(payload)

    while not stop.is_set():
        try:
            async with engine.connect() as conn:
                raw = await conn.get_raw_connection()
                driver = raw.driver_connection  # asyncpg 连接
                closed = asyncio.Event()
                driver.add_termination_listener(lambda _c, closed=closed: closed.set())
                await driver.add_listener(CHANNEL, deliver)
                if ready is not None:
                    ready.set()
                await wait_any(stop, closed, timeout=None)
                await conn.invalidate()  # LISTEN 挂在这条连接上，不还回连接池
        except Exception:
            log.warning("job notification listener disconnected, retrying", exc_info=True)
        await wait_any(stop, timeout=retry_seconds)
