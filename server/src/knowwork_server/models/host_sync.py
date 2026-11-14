"""把输入框能选的对话模型推给宿主：宿主据此声明官方模型适配器的路由（runtime-host 的 model-catalog.mjs），
没声明的模型官方适配器不认，选了也发不出去。

什么时候推：业务服务启动、本进程改了厂商或模型（catalog.on_change）、宿主重启（换了 epoch）、宿主说不认识某个模型时。
推的总是当时的整份清单；后台触发的几次合成一次，推的过程中又改了就推完再推一次。后台推失败只记日志，等下次触发。
用户设备上的本机宿主推不过去：它说不认识某个模型时，设备来取同一份清单（host_models）交给它，再发一次（同「宿主说不认识」）。
"""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from ..kernel.contract import HostModel
from .schemas import ChatModel

log = logging.getLogger(__name__)

Push = Callable[[tuple[ChatModel, ...]], Awaitable[None]]


def host_models_of(chats: tuple[ChatModel, ...]) -> tuple[HostModel, ...]:
    """推给宿主的样子（别名、名字、能不能看图）：服务端宿主与用户设备上的本机宿主收同一份（kernel/wire.py 的 models_body）。"""
    return tuple(HostModel(id=c.alias, name=c.name, vision=c.vision) for c in chats)


class ChatModelSource(Protocol):
    """ModelCatalog 满足它。"""

    async def chat_models(self) -> tuple[ChatModel, ...]: ...


class HostModelSync:
    def __init__(self, catalog: ChatModelSource, push: Push) -> None:
        self._catalog = catalog
        self._send = push
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._again = False

    async def push(self) -> None:
        """推一次，等推完；失败照常抛出，由调用方决定（发消息前重推要知道成没成）。"""
        async with self._lock:
            await self._send(await self._catalog.chat_models())

    async def host_models(self) -> tuple[HostModel, ...]:
        """此刻要给宿主的整份清单（设备上的本机宿主说不认识某个模型时，经设备来取）。"""
        return host_models_of(await self._catalog.chat_models())

    async def choosable(self, alias: str) -> bool:
        """输入框里能不能选这个模型（启用的对话模型）。"""
        return any(model.alias == alias for model in await self._catalog.chat_models())

    def changed(self) -> None:
        """模型改了：后台推一次，不等。正在推就记一笔，推完再推。"""
        if self._task is not None and not self._task.done():
            self._again = True
            return
        self._task = asyncio.create_task(self._push_until_settled(), name="host-model-sync")

    async def push_quietly(self) -> None:
        """推一次，失败只记日志（启动与宿主重启时用：宿主暂时连不上不挡别的事）。"""
        try:
            await self.push()
        except Exception as error:
            log.warning("pushing chat models to host failed: %s", type(error).__name__, exc_info=error)

    async def aclose(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _push_until_settled(self) -> None:
        while True:
            self._again = False
            await self.push_quietly()
            if not self._again:
                return
