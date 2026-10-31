"""渠道连接的启停：业务服务启动时连上全部空间里启用的机器人，管理员改了
配置就重连那一个，停机时全部断开。

长连接放在业务服务进程里：本来就单进程，会话事件也在这里。每个机器人再拿一把 Postgres
advisory 锁，误起了两份业务服务时只有一份连得上（企业微信同一个机器人新连接会把旧的踢掉，两份会来回互踢）；
拿不到锁的每 30 秒再试。
"""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ..db.base import in_workspace
from ..knowledge.crypto import SecretBox, SecretError
from .models import Channel
from .store import ChannelStore
from .types import MessageHandler

log = logging.getLogger(__name__)

LOCK_RETRY_SECONDS = 30.0
SECRET_PURPOSE = "channel-secrets"


class ChannelSecrets:
    """渠道凭据里保密的部分：JSON 加密后存 channel.secret，附加数据绑定「空间/渠道」，挪到别的行解不开。"""

    def __init__(self, master_key: str) -> None:
        self._box = SecretBox.from_master_key(master_key, SECRET_PURPOSE)

    def seal(self, workspace_id: str, channel_id: str, values: dict[str, str]) -> str:
        return self._box.seal(json.dumps(values), context=f"{workspace_id}/{channel_id}/secret")

    def open(self, channel: Channel) -> dict[str, str]:
        if not channel.secret:
            return {}
        try:
            return json.loads(self._box.open(channel.secret, context=f"{channel.workspace_id}/{channel.id}/secret"))
        except (SecretError, ValueError):
            return {}


# (渠道, 凭据明文, 收到消息时调用, 状态变化时调用) → 一个连接（有 start / stop）
ConnectionFactory = Callable[..., object]


@dataclass
class _Running:
    connection: object | None
    lock: AsyncConnection | None
    key: str = ""
    waiter: asyncio.Task | None = None


class ChannelSupervisor:
    def __init__(self, store: ChannelStore, secrets: ChannelSecrets, engine: AsyncEngine | None,
                 on_message: MessageHandler, factories: dict[str, ConnectionFactory]) -> None:
        self._store = store
        self._secrets = secrets
        self._engine = engine
        self._on_message = on_message
        self._factories = factories
        self._running: dict[str, _Running] = {}
        self._guard = asyncio.Lock()

    async def start(self) -> None:
        try:
            channels = await self._store.runnable()
        except Exception:
            log.exception("listing channels failed; no channel connected")
            return
        for channel in channels:
            await self._launch(channel)

    async def stop(self) -> None:
        async with self._guard:
            for channel_id in list(self._running):
                await self._halt(channel_id)

    async def reload(self, workspace_id: str, channel_id: str) -> None:
        """配置改了、启停了、删了：先断开旧的，还启用着就按新配置连上。"""
        async with self._guard:
            await self._halt(channel_id)
        with in_workspace(workspace_id):
            channel = await self._store.get(workspace_id, channel_id)
            if channel is None:
                return
            if not channel.enabled:
                await self._store.set_status(workspace_id, channel_id, "stopped")
                return
        await self._launch(channel)

    def running(self, channel_id: str) -> bool:
        return channel_id in self._running

    async def _launch(self, channel: Channel) -> None:
        factory = self._factories.get(channel.platform)
        if factory is None:
            return
        async with self._guard:
            if channel.id in self._running:
                return
            lock = await self._try_lock(_bot_key(channel))
            if lock is None and self._engine is not None:
                await self._report(channel, "error", "这个机器人正被另一个渠道或另一个业务服务实例连接着，30 秒后再试")
                self._running[channel.id] = _Running(None, None, asyncio.create_task(self._wait_lock(channel)))
                return
            connection = factory(channel, self._secrets.open(channel), self._on_message,
                                 lambda status, error="", c=channel: self._report(c, status, error))
            self._running[channel.id] = _Running(connection, lock, _bot_key(channel))
        try:
            await connection.start()
        except Exception as error:
            log.exception("starting channel %s failed", channel.id)
            await self._report(channel, "error", str(error)[:300])

    async def _wait_lock(self, channel: Channel) -> None:
        await asyncio.sleep(LOCK_RETRY_SECONDS)
        async with self._guard:
            self._running.pop(channel.id, None)
        await self._launch(channel)

    async def _halt(self, channel_id: str) -> None:
        running = self._running.pop(channel_id, None)
        if running is None:
            return
        if running.waiter is not None:
            running.waiter.cancel()
        if running.connection is not None:
            try:
                await running.connection.stop()
            except Exception:
                log.warning("stopping channel %s failed", channel_id, exc_info=True)
        if running.lock is not None:
            await _unlock(running.lock, running.key)

    async def _try_lock(self, key: str) -> AsyncConnection | None:
        if self._engine is None:
            return None
        connection = await self._engine.connect()
        try:
            held = await connection.scalar(text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                                           {"key": key})
            await connection.commit()
        except Exception:
            await connection.close()
            raise
        if not held:
            await connection.close()
            return None
        return connection

    async def _report(self, channel: Channel, status: str, error: str = "") -> None:
        try:
            with in_workspace(channel.workspace_id):
                await self._store.set_status(channel.workspace_id, channel.id, status, error)
        except Exception:
            log.warning("recording channel %s status failed", channel.id, exc_info=True)


def _bot_key(channel: Channel) -> str:
    """锁按机器人加（平台 + Bot ID），不按渠道：同一个机器人配成两个渠道（哪怕在不同空间）也只连一份。"""
    bot = str((channel.public_config or {}).get("bot_id") or channel.id)
    return f"knowwork.channel:{channel.platform}:{bot}"


async def _unlock(connection: AsyncConnection, key: str) -> None:
    """会话级的锁跟着数据库连接走，连接还回连接池并不会断开，所以先显式解锁再还。"""
    try:
        await connection.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": key})
        await connection.commit()
    except Exception:
        log.warning("releasing channel lock %s failed; dropping the connection", key, exc_info=True)
        await connection.invalidate()
    finally:
        await connection.close()
