"""钉钉机器人（Stream 模式），用钉钉官方 Python SDK dingtalk-stream（MIT）：建连、心跳、重连、回执由它管。

- 服务器主动连钉钉，不用公网回调地址（PRD 6.19；钉钉的 HTTP 回调签名只认时间戳、不认消息体，WeKnora 也只允许
  Stream 模式）。群里只有 @ 机器人的消息会推过来。
- 流式用 SDK 内置的 AI Markdown 卡片（公共模板，管理员不用自己建模板）：开始 → 打字机更新 → 完成。卡片要应用
  开通「互动卡片」相关权限；发不出去时退回用这条消息带的会话 webhook 回一条 markdown。
- 回答里的图：卡片 markdown 只认公网图片地址，内网部署员工手机打不开，所以答完后每张图用 SDK 的
  upload_to_dingtalk 传上去换 mediaId，再用机器人发消息接口单独发一条图片消息（sampleImageMsg，photoURL 填
  mediaId；群里按会话 id、私聊按员工 id 发）。要应用开通「企业内机器人发送消息」权限，没开就只有文字。

线程：SDK 的 start() 重连时用 requests 同步换连接地址、不设超时，放在业务服务的主循环里网络一卡就卡住整个服务，
所以每个机器人在自己的线程里跑自己的循环；收到的消息转交回主循环处理。回复都是同步的 HTTP 调用，放线程池执行。
SDK 的启动循环会吞掉取消信号，停的时候直接让那个线程的循环停下。SDK 没有「已连接」的回调，看 websocket 状态。
"""

import asyncio
import json
import logging
import threading
from collections.abc import Sequence
from typing import Any

import httpx
from dingtalk_stream import (
    AckMessage,
    CallbackHandler,
    ChatbotHandler,
    ChatbotMessage,
    Credential,
    DingTalkStreamClient,
)
from dingtalk_stream.card_instance import AIMarkdownCardInstance

from .types import IncomingMessage, MessageHandler, Picture, StatusReporter

log = logging.getLogger(__name__)

WATCH_SECONDS = 3.0
OPENAPI = "https://api.dingtalk.com/v1.0/robot"
DINGTALK_IMAGE_BYTES = 10 * 1024 * 1024
HTTP_TIMEOUT = 15
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/bmp": "bmp"}
GROUP = "2"  # conversation_type：1 单聊，2 群聊


def strip_mentions(text: str) -> str:
    text = text.strip()
    while text.startswith("@"):
        _, sep, rest = text.partition(" ")
        if not sep:
            return ""
        text = rest.strip()
    return text


def _is_open(ws: Any) -> bool:
    state = getattr(ws, "state", None)
    if state is not None:
        return getattr(state, "name", "") == "OPEN"
    return bool(getattr(ws, "open", False))


class _Received(CallbackHandler):
    """SDK 的回调处理器：在机器人线程的循环里被调用，马上回执，消息交给连接转到主循环。"""

    def __init__(self, connection: "DingtalkConnection") -> None:
        super().__init__()
        self._connection = connection

    async def process(self, callback):
        self._connection.received(ChatbotMessage.from_dict(callback.data))
        return AckMessage.STATUS_OK, "OK"


class DingtalkStream:
    def __init__(self, connection: "DingtalkConnection", message: IncomingMessage) -> None:
        self._connection = connection
        self._message = message
        self._card: Any = None

    async def start(self, placeholder: str) -> None:
        card = self._connection.card(self._message.raw)
        try:
            await asyncio.to_thread(card.ai_start)
        except Exception:
            log.warning("dingtalk channel %s could not send a card", self._connection.channel_id, exc_info=True)
            return
        if card.card_instance_id:
            self._card = card
            await asyncio.to_thread(card.ai_streaming, placeholder, False)

    async def update(self, text: str) -> None:
        if self._card is not None:
            await asyncio.to_thread(self._card.ai_streaming, text or " ", False)

    async def finish(self, text: str, pictures: Sequence[Picture] = ()) -> None:
        if self._card is not None:
            await asyncio.to_thread(self._card.ai_finish, text or " ")
        else:
            await self._connection.reply(self._message, text)  # 卡片没发出去：用会话 webhook 回一条
        if pictures:
            await asyncio.to_thread(self._connection.send_pictures, self._message, pictures)


class DingtalkConnection:
    def __init__(self, *, channel_id: str, workspace_id: str, client_id: str, secret: str,
                 on_message: MessageHandler, on_status: StatusReporter, client_factory: Any = DingTalkStreamClient,
                 card_factory: Any = AIMarkdownCardInstance) -> None:
        self.channel_id = channel_id
        self.workspace_id = workspace_id
        self._client_id = client_id
        self._secret = secret
        self._on_message = on_message
        self._on_status = on_status
        self._client_factory = client_factory
        self._card_factory = card_factory
        self.client: Any = None
        self._replier = ChatbotHandler()
        self._main: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._thread_loop: asyncio.AbstractEventLoop | None = None
        self._watcher: asyncio.Task | None = None
        self._stopping = threading.Event()

    async def start(self) -> None:
        self._main = asyncio.get_running_loop()
        self.client = self._client_factory(Credential(self._client_id, self._secret), logger=log)
        self.client.register_callback_handler(ChatbotMessage.TOPIC, _Received(self))
        self._replier.dingtalk_client = self.client
        await self._on_status("connecting", "")
        # SDK 换不到连接地址时只打日志、每 10 秒重试；先换一次，凭据不对能在管理页看到原因
        if await asyncio.to_thread(self.client.open_connection) is None:
            await self._on_status("error", "认证失败，请检查 Client ID 与 Client Secret，并确认应用开了机器人的 Stream 模式")
        self._thread = threading.Thread(target=self._run, name=f"dingtalk-{self.channel_id}", daemon=True)
        self._thread.start()
        self._watcher = asyncio.create_task(self._watch())

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._thread_loop = loop
        try:
            loop.run_until_complete(self.client.start())
        except RuntimeError:
            if not self._stopping.is_set():
                log.warning("dingtalk channel %s loop ended", self.channel_id, exc_info=True)
        finally:
            asyncio.set_event_loop(None)
            try:
                loop.close()
            except RuntimeError:  # 还有没收尾的任务时关不掉，交给进程退出
                log.debug("dingtalk channel %s loop left open", self.channel_id)

    async def _watch(self) -> None:
        reported = ""
        while not self._stopping.is_set():
            ws = getattr(self.client, "websocket", None)
            status = "connected" if ws is not None and _is_open(ws) else "connecting"
            if status != reported and (status == "connected" or reported):
                await self._on_status(status, "")
            reported = status
            await asyncio.sleep(WATCH_SECONDS)

    async def stop(self) -> None:
        self._stopping.set()
        if self._watcher is not None:
            self._watcher.cancel()
        loop = self._thread_loop
        if loop is not None and loop.is_running():
            ws = getattr(self.client, "websocket", None)
            if ws is not None:
                try:
                    await asyncio.wait_for(asyncio.wrap_future(asyncio.run_coroutine_threadsafe(ws.close(), loop)), 3)
                except Exception:
                    log.warning("dingtalk channel %s close failed", self.channel_id, exc_info=True)
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 10)

    def received(self, incoming: ChatbotMessage) -> None:
        """在机器人线程里被调用：转成消息，交回主循环。"""
        message = self.convert(incoming)
        if message is not None and self._main is not None:
            asyncio.run_coroutine_threadsafe(self._on_message(message, self), self._main)

    def convert(self, incoming: ChatbotMessage) -> IncomingMessage | None:
        external_id = str(incoming.sender_staff_id or incoming.sender_id or "")
        if not external_id:
            return None
        group = str(incoming.conversation_type) == GROUP
        text = str(getattr(incoming.text, "content", "") or "") if incoming.text is not None else ""
        if group:
            text = strip_mentions(text)
        return IncomingMessage(channel_id=self.channel_id, workspace_id=self.workspace_id, platform="dingtalk",
                               external_id=external_id, chat_type="group" if group else "direct",
                               chat_id=str(incoming.conversation_id or "") if group else "",
                               message_id=str(incoming.message_id or ""), text=text.strip(),
                               display_name=str(incoming.sender_nick or external_id), raw=incoming)

    def card(self, incoming: ChatbotMessage) -> Any:
        return self._card_factory(self.client, incoming)

    async def reply(self, message: IncomingMessage, text: str) -> None:
        result = await asyncio.to_thread(self._replier.reply_markdown, "回复", text, message.raw)
        if result is None:
            raise RuntimeError("dingtalk reply failed")

    async def stream(self, message: IncomingMessage, placeholder: str) -> DingtalkStream:
        stream = DingtalkStream(self, message)
        await stream.start(placeholder)
        return stream

    def send_pictures(self, message: IncomingMessage, pictures: Sequence[Picture]) -> None:
        """同步 HTTP，在线程池里调。一张发不出去就记日志跳过；接口没权限时后面的也不再试。"""
        incoming: ChatbotMessage = message.raw
        target = ({"openConversationId": message.chat_id} if message.chat_type == "group"
                  else {"userIds": [message.external_id]})
        path = "groupMessages/send" if message.chat_type == "group" else "oToMessages/batchSend"
        robot = str(getattr(incoming, "robot_code", "") or self._client_id)
        for picture in pictures:
            extension = EXTENSIONS.get(picture.mime)
            if extension is None or len(picture.data) > DINGTALK_IMAGE_BYTES:
                continue
            try:
                media = self.client.upload_to_dingtalk(picture.data, filetype="image", filename=f"image.{extension}",
                                                       mimetype=picture.mime)
                if not media:
                    raise RuntimeError("upload returned no media id")
                response = httpx.post(f"{OPENAPI}/{path}", timeout=HTTP_TIMEOUT, headers={
                    "x-acs-dingtalk-access-token": self.client.get_access_token() or "",
                    "Content-Type": "application/json"}, json={
                    "robotCode": robot, "msgKey": "sampleImageMsg",
                    "msgParam": json.dumps({"photoURL": media}), **target})
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                log.warning("dingtalk channel %s could not send a picture: %s %s", self.channel_id,
                            error.response.status_code, error.response.text[:300])
                if error.response.status_code in (401, 403):
                    return  # 多半是没开「企业内机器人发送消息」权限
            except Exception:  # 一张图发不出去不影响回答
                log.warning("dingtalk channel %s could not send a picture", self.channel_id, exc_info=True)
