"""LISTEN 任务通知：独占一条数据库连接，收到 NOTIFY 就唤醒对应的执行槽（通用部分在 db/listen.py）。

连接断了隔几秒重连；断开期间执行槽靠轮询兜底，任务不会漏，只是晚几秒被领取。
"""

import asyncio
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncEngine

from ..db.listen import listen as listen_channel
from .queue import CHANNEL


async def listen(engine: AsyncEngine, on_message: Callable[[str], None], stop: asyncio.Event, *,
                 retry_seconds: float = 5.0, ready: asyncio.Event | None = None) -> None:
    await listen_channel(engine, CHANNEL, on_message, stop, retry_seconds=retry_seconds, ready=ready)
