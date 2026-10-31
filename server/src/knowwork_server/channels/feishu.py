"""飞书机器人（长连接），用飞书官方 SDK 的 IM 渠道组件 lark_oapi.channel.FeishuChannel（MIT）：长连接、重连、
消息去重与归一、@ 解析、按会话排队、卡片流式（CardKit，带节流与按序号更新）都由它管。

- 服务器主动连飞书，不用公网回调地址（PRD 6.19）。群里默认只有 @ 机器人的消息才处理（SDK 的 require_mention）。
- 回复挂在原消息下面；流式用 SDK 的 markdown 流式卡片（先建一张开了流式模式的卡片，再按序号更新正文）。
- 卡片不认外链图片：回答里的图答完时用 SDK 的 upload_media 传上去换 image_key，写成 ![描述](image_key) 接在
  回答后面；传不上去的跳过。

两个线程的事要分清：
1. SDK 的长连接 start() 会阻塞，放在每个机器人自己的线程里跑；SDK 收到消息后在它自己的后台循环
   （lark-channel-bg 线程）里调我们注册的处理函数，所以收到的消息要转交回业务服务的主循环处理（数据库连接、
   会话服务都在主循环上）；回复与流式则放回 SDK 的后台循环执行（channel.schedule），它内部的锁和队列建在那里。
2. SDK 底层长连接客户端（lark_oapi.ws.client）用的是模块级的事件循环 `loop`，一个进程只有一个：配两个飞书机器人
   时第二个会报「循环已在运行」（KnowFlow 当时继承客户端改写了私有方法绕开）。这里把这个模块属性换成按线程取的
   代理：每个机器人的线程建自己的循环，SDK 引用 `loop` 时拿到的就是当前线程的那个，只动一处模块属性。
"""

import asyncio
import logging
import queue
import threading
from collections.abc import Sequence
from typing import Any

import lark_oapi.ws.client as _ws_client_module
from lark_oapi import LogLevel
from lark_oapi.channel import FeishuChannel, PolicyConfig
from lark_oapi.channel.types import MediaSource

from .types import IncomingMessage, MessageHandler, Picture, StatusReporter

log = logging.getLogger(__name__)

FEISHU_IMAGE_BYTES = 10 * 1024 * 1024  # 飞书上传图片的上限
RETRY_SECONDS = 30.0  # 启动失败（凭据不对、网络不通）后隔这么久再试
WATCH_SECONDS = 3.0


class _ThreadLoop:
    """lark_oapi.ws.client.loop 的替身：取当前线程的事件循环。每个机器人的线程先 set_event_loop 再启动。"""

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio.get_event_loop(), name)


def _install_thread_loop() -> None:
    if not isinstance(getattr(_ws_client_module, "loop", None), _ThreadLoop):
        _ws_client_module.loop = _ThreadLoop()


def strip_mentions(text: str) -> str:
    """去掉开头的 @（SDK 归一后可能是 @名字，也可能还是 @_user_1 占位符）。"""
    text = text.strip()
    while text.startswith("@"):
        _, sep, rest = text.partition(" ")
        if not sep:
            return ""
        text = rest.strip()
    return text


class FeishuStream:
    """把我们「推」的 update/finish 接到 SDK「拉」的 producer 上：中间一个线程安全的队列。"""

    def __init__(self, connection: "FeishuConnection", message: IncomingMessage, placeholder: str) -> None:
        self._connection = connection
        self._message = message
        self._placeholder = placeholder
        self._updates: queue.Queue = queue.Queue()
        self._result = None

    def begin(self) -> None:
        self._result = self._connection.schedule(self._connection.channel.stream(
            self._message.chat_id, {"markdown": self._producer}, {"reply_to": self._message.message_id}))

    async def _producer(self, controller) -> None:
        loop = asyncio.get_running_loop()
        await controller.set_content(self._placeholder)
        while True:
            kind, text = await loop.run_in_executor(None, self._updates.get)
            await controller.set_content(text or " ")
            if kind == "finish":
                return

    async def update(self, text: str) -> None:
        self._updates.put(("update", text))

    async def finish(self, text: str, pictures: Sequence[Picture] = ()) -> None:
        keys = await self._connection.upload_pictures(pictures)
        images = "\n\n".join(f"![{_alt(caption)}]({key})" for key, caption in keys)
        self._updates.put(("finish", f"{text}\n\n{images}" if images else text))
        if self._result is not None:
            await asyncio.wrap_future(self._result)


def _alt(caption: str) -> str:
    """图的悬停文字：markdown 的方括号与换行会把图片语法弄坏，去掉。"""
    return " ".join(caption.replace("[", "").replace("]", "").split())[:60] or "图片"


class FeishuConnection:
    def __init__(self, *, channel_id: str, workspace_id: str, app_id: str, secret: str,
                 on_message: MessageHandler, on_status: StatusReporter, channel_factory: Any = FeishuChannel) -> None:
        self.channel_id = channel_id
        self.workspace_id = workspace_id
        self._app_id = app_id
        self._secret = secret
        self._on_message = on_message
        self._on_status = on_status
        self._factory = channel_factory
        self.channel: Any = None
        self._main: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._thread_loop: asyncio.AbstractEventLoop | None = None
        self._watcher: asyncio.Task | None = None
        self._stopping = threading.Event()
        self._last_error = ""

    async def start(self) -> None:
        _install_thread_loop()
        self._main = asyncio.get_running_loop()
        self.channel = self._factory(app_id=self._app_id, app_secret=self._secret, log_level=LogLevel.WARNING,
                                     policy=PolicyConfig(require_mention=True))
        self.channel.on("message", self._received)
        self.channel.on("error", lambda error: self._error(str(error)))
        await self._on_status("connecting", "")
        self._thread = threading.Thread(target=self._run, name=f"feishu-{self.channel_id}", daemon=True)
        self._thread.start()
        self._watcher = asyncio.create_task(self._watch())

    def _run(self) -> None:
        """机器人自己的线程：建自己的循环，阻塞跑 SDK 的长连接；启动失败隔一会儿再试，直到停下。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._thread_loop = loop
        try:
            while not self._stopping.is_set():
                try:
                    self.channel.start()
                except Exception as error:
                    if self._stopping.is_set():
                        break  # stop() 让循环停下时 start() 会抛「循环在 Future 完成前停止」
                    log.warning("feishu channel %s failed to connect", self.channel_id, exc_info=True)
                    self._error(_reason(error))  # 凭据不对、网络不通：记下原因，过一会儿再试
                    self._stopping.wait(RETRY_SECONDS)
                    continue
                break
        finally:
            # SDK 的收尾在这个线程里、循环停下之后调：它按「当前线程的循环」断开长连接（见模块说明），
            # 在主线程调会拿到业务服务的主循环
            try:
                self.channel.stop()
            except Exception:
                log.warning("feishu channel %s stop failed", self.channel_id, exc_info=True)
            asyncio.set_event_loop(None)
            try:
                loop.close()
            except RuntimeError:  # 还有没收尾的任务时关不掉，交给进程退出
                log.debug("feishu channel %s loop left open", self.channel_id)

    async def _watch(self) -> None:
        """SDK 没有「已连接」的回调：看长连接还在不在，变了就报。"""
        reported = ""
        while not self._stopping.is_set():
            ws = getattr(self.channel, "ws_client", None)
            status = "connected" if getattr(ws, "_conn", None) is not None else \
                ("error" if self._last_error else "connecting")
            if status != reported:
                reported = status
                await self._on_status(status, self._last_error if status == "error" else "")
                if status == "connected":
                    self._last_error = ""
            await asyncio.sleep(WATCH_SECONDS)

    def _error(self, reason: str) -> None:
        self._last_error = reason[:300]

    async def stop(self) -> None:
        """断开长连接、让机器人线程的循环停下，线程自己做 SDK 的收尾。不在这里调 channel.stop()（见 _run）。"""
        self._stopping.set()
        if self._watcher is not None:
            self._watcher.cancel()
        ws, loop = getattr(self.channel, "ws_client", None), self._thread_loop
        if ws is not None and loop is not None and loop.is_running():
            ws._auto_reconnect = False  # SDK 没有公开的停止长连接的方法，只能动这两处
            try:
                await asyncio.wait_for(asyncio.wrap_future(asyncio.run_coroutine_threadsafe(ws._disconnect(), loop)),
                                       3)
            except Exception:
                log.warning("feishu channel %s disconnect failed", self.channel_id, exc_info=True)
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 10)

    def schedule(self, coroutine):
        """放到 SDK 的后台循环里执行，返回 concurrent.futures.Future。"""
        return self.channel.schedule(coroutine)

    def _received(self, inbound: Any) -> None:
        """在 SDK 的后台循环里被调用：转成消息，交回主循环。"""
        message = self.convert(inbound)
        if message is not None and self._main is not None:
            asyncio.run_coroutine_threadsafe(self._on_message(message, self), self._main)

    def convert(self, inbound: Any) -> IncomingMessage | None:
        sender = getattr(inbound, "sender", None)
        external_id = str(getattr(sender, "open_id", "") or "")
        if not external_id or getattr(sender, "is_bot", False):
            return None
        conversation = getattr(inbound, "conversation", None)
        group = getattr(conversation, "chat_type", "") in ("group", "topic")
        text = str(getattr(inbound, "content_text", "") or "")
        if group:
            text = strip_mentions(text)
        return IncomingMessage(channel_id=self.channel_id, workspace_id=self.workspace_id, platform="feishu",
                               external_id=external_id, chat_type="group" if group else "direct",
                               chat_id=str(getattr(conversation, "chat_id", "") or ""),
                               message_id=str(getattr(inbound, "id", "") or ""), text=text,
                               display_name=str(getattr(sender, "display_name", "") or external_id), raw=inbound)

    async def reply(self, message: IncomingMessage, text: str) -> None:
        await asyncio.wrap_future(self.schedule(self.channel.send(
            message.chat_id, {"markdown": text}, {"reply_to": message.message_id})))

    async def stream(self, message: IncomingMessage, placeholder: str) -> FeishuStream:
        stream = FeishuStream(self, message, placeholder)
        stream.begin()
        return stream

    async def upload_pictures(self, pictures: Sequence[Picture]) -> list[tuple[str, str]]:
        """传图换 image_key（在 SDK 的后台循环里传），返回 (image_key, 描述)；传不上去的跳过。"""
        keys: list[tuple[str, str]] = []
        for picture in pictures:
            if len(picture.data) > FEISHU_IMAGE_BYTES:
                continue
            try:
                key = await asyncio.wrap_future(self.schedule(self.channel.upload_media(
                    MediaSource(kind="buffer", buffer=picture.data), kind="image")))
            except Exception:  # 一张图传不上去不影响回答
                log.warning("feishu channel %s could not upload a picture", self.channel_id, exc_info=True)
                continue
            if key:
                keys.append((str(key), picture.caption))
        return keys


def _reason(error: object) -> str:
    text = str(error or "连接出错")
    lowered = text.lower()
    if "app_secret" in lowered or "app_id" in lowered or "credential" in lowered or "invalid" in lowered:
        return f"认证失败，请检查 App ID 与 App Secret（{text[:200]}）"
    return text[:300]
