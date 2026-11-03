"""LISTEN 数据库通知：独占一条连接，收到 NOTIFY 就回调。任务队列（jobs）与权限规则的跨进程刷新（access）共用。

连接断了隔几秒重连；断开期间的通知会丢，所以每次（重新）连上都回调 on_connected，调用方据此补一次（重新领任务、
重新加载规则），不会因为漏了通知一直停在旧状态。
"""

import asyncio
import logging
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)


async def _wait(stop: asyncio.Event, other: asyncio.Event | None, timeout: float | None) -> None:
    waiters = [asyncio.ensure_future(stop.wait())]
    if other is not None:
        waiters.append(asyncio.ensure_future(other.wait()))
    try:
        await asyncio.wait(waiters, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for waiter in waiters:
            waiter.cancel()


async def listen(engine: AsyncEngine, channel: str, on_message: Callable[[str], None], stop: asyncio.Event, *,
                 retry_seconds: float = 5.0, ready: asyncio.Event | None = None,
                 on_connected: Callable[[], None] | None = None) -> None:
    def deliver(_connection, _pid, _channel, payload: str) -> None:
        on_message(payload)

    while not stop.is_set():
        try:
            async with engine.connect() as conn:
                raw = await conn.get_raw_connection()
                driver = raw.driver_connection  # asyncpg 连接
                closed = asyncio.Event()
                driver.add_termination_listener(lambda _c, closed=closed: closed.set())
                await driver.add_listener(channel, deliver)
                if on_connected is not None:
                    on_connected()
                if ready is not None:
                    ready.set()
                await _wait(stop, closed, None)
                await conn.invalidate()  # LISTEN 挂在这条连接上，不还回连接池
        except Exception:
            log.warning("notification listener on %s disconnected, retrying", channel, exc_info=True)
        await _wait(stop, None, retry_seconds)
