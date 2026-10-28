"""企业微信智能机器人（API 模式 · 长连接），用企业微信官方 Python SDK（wecom-aibot-python-sdk，MIT）：
连接、认证、心跳、断线重连、回复回执都由 SDK 管，这里只把帧转成 IncomingMessage、把回复交回去。

- 服务器主动连 wss://openws.work.weixin.qq.com，客户内网不用开入站端口（PRD 6.19）。
- 群里只有 @ 机器人的消息会推过来，内容开头带「@机器人名」，去掉再处理（照 KnowFlow api/im/wecom_ws.py）。
- 流式回复：同一个 stream id 反复发到目前为止的全文，最后 finish。流式消息有时限，超时企业微信回 846608，
  这时改成主动推一条完整消息（社区插件 dsh-channel-wecom 踩过的坑）。
- 回答里的图：答完先发文字，再每张图经长连接分片上传成临时素材（aibot_upload_media_init / chunk / finish），拿
  media_id 回一条图片消息。Python SDK 1.0.2 还没有这两个方法，照官方 Node SDK（@wecom/aibot-node-sdk 1.0.7 的
  uploadMedia、replyMedia）用 SDK 的底层发送接口实现。文档里流式结束帧的图文混排（msg_item）实测企业微信回执成功
  但不显示图，不用。
- 首次连接失败时 SDK 会在 connect() 里一直重连、不返回，所以放进后台任务。
- 同一个机器人在别处建了新连接，企业微信会推 disconnected_event 把这条断掉：标成出错，提示管理员。
"""

import asyncio
import base64
import hashlib
import logging
from collections.abc import Sequence
from typing import Any

from aibot import WSClient, WSClientOptions, generate_req_id

from .types import IncomingMessage, MessageHandler, Picture, StatusReporter

log = logging.getLogger(__name__)

STREAM_EXPIRED = "846608"
WECOM_IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg"}
WECOM_IMAGE_BYTES = 10 * 1024 * 1024
CHUNK_BYTES = 512 * 1024  # 单个分片（base64 编码前）的上限，同 Node SDK
UPLOAD_INIT, UPLOAD_CHUNK, UPLOAD_FINISH = ("aibot_upload_media_init", "aibot_upload_media_chunk",
                                            "aibot_upload_media_finish")
MESSAGE_EVENTS = ("message.text", "message.mixed", "message.voice")


class _SdkLogger:
    """SDK 的日志接到业务服务的日志里（它自带的 DefaultLogger 直接 print）。"""

    def debug(self, message: str, *args: object) -> None:
        log.debug("wecom sdk: %s", message)

    def info(self, message: str, *args: object) -> None:
        log.debug("wecom sdk: %s", message)

    def warn(self, message: str, *args: object) -> None:
        log.info("wecom sdk: %s", message)

    def error(self, message: str, *args: object) -> None:
        log.warning("wecom sdk: %s", message)


def _text_of(body: dict) -> str:
    kind = body.get("msgtype")
    if kind == "text":
        return str((body.get("text") or {}).get("content") or "")
    if kind == "voice":  # 企业微信已经转好文字
        return str((body.get("voice") or {}).get("content") or "")
    if kind == "mixed":
        items = (body.get("mixed") or {}).get("msg_item") or []
        return "\n".join(str((item.get("text") or {}).get("content") or "") for item in items
                         if item.get("msgtype") == "text").strip()
    return ""


def strip_mention(text: str, bot_name: str) -> str:
    """去掉群消息开头的「@机器人名」。名字不知道时去掉开头第一个 @xxx 词。"""
    text = text.strip()
    if bot_name and text.startswith(f"@{bot_name}"):
        return text[len(bot_name) + 1:].strip()
    if text.startswith("@"):
        head, _, rest = text.partition(" ")
        return rest.strip() if rest else ("" if head else text)
    return text


async def upload_media(client: Any, data: bytes, filename: str, kind: str = "image") -> str:
    """经长连接分片上传一个临时素材，返回 media_id（三步同 Node SDK 的 uploadMedia，分片按顺序传）。"""
    manager = client._ws_manager  # SDK 没公开这个对象；回执、排队都用它的
    chunks = [data[start:start + CHUNK_BYTES] for start in range(0, len(data), CHUNK_BYTES)] or [b""]
    init = await manager.send_reply(generate_req_id(UPLOAD_INIT), {
        "type": kind, "filename": filename, "total_size": len(data), "total_chunks": len(chunks),
        "md5": hashlib.md5(data).hexdigest()}, UPLOAD_INIT)  # 平台要求的校验值
    upload_id = (init.get("body") or {}).get("upload_id")
    if not upload_id:
        raise RuntimeError(f"upload init returned no upload_id: {init.get('errmsg', '')}")
    for index, chunk in enumerate(chunks):
        await manager.send_reply(generate_req_id(UPLOAD_CHUNK), {
            "upload_id": upload_id, "chunk_index": index, "base64_data": base64.b64encode(chunk).decode()},
            UPLOAD_CHUNK)
    done = await manager.send_reply(generate_req_id(UPLOAD_FINISH), {"upload_id": upload_id}, UPLOAD_FINISH)
    media_id = (done.get("body") or {}).get("media_id")
    if not media_id:
        raise RuntimeError(f"upload finish returned no media_id: {done.get('errmsg', '')}")
    return str(media_id)


class WecomStream:
    def __init__(self, connection: "WecomConnection", message: IncomingMessage) -> None:
        self._connection = connection
        self._message = message
        self._id = generate_req_id("stream")
        self._expired = False

    async def start(self, placeholder: str) -> None:
        await self._send(placeholder, finish=False)

    async def update(self, text: str) -> None:
        if not self._expired:
            await self._send(text, finish=False)

    async def finish(self, text: str, pictures: Sequence[Picture] = ()) -> None:
        if not self._expired:
            await self._send(text, finish=True)
        if self._expired:
            await self._connection.push(self._message, text)
        await self._connection.send_pictures(self._message, pictures, passive=not self._expired)

    async def _send(self, text: str, *, finish: bool) -> None:
        client = self._connection.client
        if client is None:
            raise RuntimeError("wecom connection is closed")
        try:
            await client.reply_stream(self._message.raw, self._id, text or " ", finish=finish)
        except RuntimeError as error:
            if STREAM_EXPIRED in str(error):
                self._expired = True
                return
            raise


class WecomConnection:
    def __init__(self, *, channel_id: str, workspace_id: str, bot_id: str, secret: str, bot_name: str = "",
                 on_message: MessageHandler, on_status: StatusReporter, ws_url: str = "",
                 client_factory: Any = WSClient) -> None:
        self.channel_id = channel_id
        self.workspace_id = workspace_id
        self._bot_id = bot_id
        self._secret = secret
        self._bot_name = bot_name
        self._on_message = on_message
        self._on_status = on_status
        self._ws_url = ws_url
        self._factory = client_factory
        self.client: Any = None
        self._connecting: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        options = WSClientOptions(bot_id=self._bot_id, secret=self._secret, max_reconnect_attempts=-1,
                                  ws_url=self._ws_url, logger=_SdkLogger())
        client = self._factory(options)
        for name in MESSAGE_EVENTS:
            client.on(name, self._received)
        client.on("authenticated", lambda: self._status("connected"))
        client.on("disconnected", lambda reason=None: self._status("connecting", str(reason or "")))
        client.on("error", lambda error=None: self._status("error", _reason(error, self._bot_id)))
        client.on("event.disconnected_event", lambda frame=None: self._status(
            "error", "这个机器人在别处建立了新连接，企业微信断开了本连接"))
        self.client = client
        await self._on_status("connecting", "")
        self._connecting = asyncio.create_task(client.connect())

    async def stop(self) -> None:
        client, self.client = self.client, None
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                log.warning("wecom channel %s disconnect failed", self.channel_id, exc_info=True)
        if self._connecting is not None:
            self._connecting.cancel()
        for task in list(self._tasks):
            task.cancel()

    def _status(self, status: str, error: str = "") -> None:
        self._spawn(self._on_status(status, error))

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _received(self, frame: dict) -> None:
        message = self.convert(frame)
        if message is not None:
            self._spawn(self._on_message(message, self))

    def convert(self, frame: dict) -> IncomingMessage | None:
        body = frame.get("body") or {}
        sender = body.get("from") or {}
        external_id = str(sender.get("userid") or "")
        group = body.get("chattype") == "group"
        text = _text_of(body)
        if group:
            text = strip_mention(text, self._bot_name)
        if not external_id:
            return None
        return IncomingMessage(channel_id=self.channel_id, workspace_id=self.workspace_id, platform="wecom",
                               external_id=external_id, chat_type="group" if group else "direct",
                               chat_id=str(body.get("chatid") or "") if group else "",
                               message_id=str(body.get("msgid") or ""), text=text,
                               display_name=external_id, raw=frame)

    async def reply(self, message: IncomingMessage, text: str) -> None:
        if self.client is None:
            raise RuntimeError("wecom connection is closed")
        await self.client.reply(message.raw, {"msgtype": "markdown", "markdown": {"content": text}})

    async def stream(self, message: IncomingMessage, placeholder: str) -> WecomStream:
        stream = WecomStream(self, message)
        await stream.start(placeholder)
        return stream

    async def push(self, message: IncomingMessage, text: str) -> None:
        """主动推一条（不依赖收到的那一帧）：私聊发给这个人，群里发到群。"""
        if self.client is None:
            raise RuntimeError("wecom connection is closed")
        target = message.chat_id or message.external_id
        await self.client.send_message(target, {"msgtype": "markdown", "markdown": {"content": text}})

    async def send_pictures(self, message: IncomingMessage, pictures: Sequence[Picture], *, passive: bool) -> None:
        """每张图传成临时素材再发一条图片消息；passive 时挂在收到的那一帧上回（同 replyMedia），流式过期了就主动推
        （同 sendMediaMessage）。只收 jpg、png，单张不超过 10MB；一张发不出去跳过。"""
        for picture in pictures:
            extension = WECOM_IMAGE_TYPES.get(picture.mime)
            if self.client is None or extension is None or len(picture.data) > WECOM_IMAGE_BYTES:
                continue
            try:
                media_id = await upload_media(self.client, picture.data, f"image.{extension}")
                body = {"msgtype": "image", "image": {"media_id": media_id}}
                if passive:
                    await self.client.reply(message.raw, body)
                else:
                    await self.client.send_message(message.chat_id or message.external_id, body)
            except Exception:  # 一张图发不出去不影响回答
                log.warning("wecom channel %s could not send a picture", self.channel_id, exc_info=True)


def _reason(error: object, bot_id: str = "") -> str:
    text = str(error or "连接出错")
    if "853000" in text or "invalid bot_id" in text:
        if bot_id.strip().isdigit():
            # 最常见的填错：拿了自建应用的 AgentId（1000002 这种纯数字）与应用 Secret（2026-10-27 实测）
            return ("Bot ID 或 Secret 不对：要用企业微信「智能机器人」API 模式（使用长连接）页面上的 Bot ID 与 Secret，"
                    f"不是自建应用的 AgentId 与 Secret（{text[:160]}）")
        return ("Bot ID 与 Secret 对不上：请确认机器人的 API 模式选了「使用长连接」并已保存；Secret 用「点击获取」拿到的"
                f"最新值（再点一次会重新生成，旧的随即失效）；两项来自同一个机器人（{text[:120]}）")
    if "Authentication failed" in text or "auth" in text.lower():
        return f"认证失败，请检查 Bot ID 与 Secret（{text[:200]}）"
    return text[:300]
