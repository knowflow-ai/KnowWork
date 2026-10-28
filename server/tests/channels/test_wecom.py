"""企业微信适配器（官方 SDK 换成假的客户端）：帧转成消息（私聊、群里去掉 @、图文混排、语音转文字）；流式回复、
过期（846608）后改成主动推；回答里的图传成素材再发图片消息；SDK 的连接事件变成渠道状态。"""

import asyncio
import base64
import hashlib

import pytest

from knowwork_server.channels.types import Picture
from knowwork_server.channels.wecom import WecomConnection, strip_mention
from knowwork_server.channels.wecom import _reason as wecom_reason

pytestmark = pytest.mark.anyio


class FakeUploads:
    """SDK 底层的发送对象：记下上传素材的三步，回 upload_id、media_id。"""

    def __init__(self):
        self.frames = []

    async def send_reply(self, req_id, body, cmd):
        self.frames.append((cmd, body))
        if cmd == "aibot_upload_media_init":
            return {"errcode": 0, "body": {"upload_id": f"up{len(self.frames)}"}}
        if cmd == "aibot_upload_media_finish":
            return {"errcode": 0, "body": {"media_id": f"media-{body['upload_id']}"}}
        return {"errcode": 0}


class FakeClient:
    def __init__(self, options):
        self.options = options
        self.handlers = {}
        self.sent = []
        self.expire_streams = False
        self._ws_manager = FakeUploads()

    def on(self, name, handler):
        self.handlers[name] = handler

    async def connect(self):
        self.handlers["authenticated"]()

    def disconnect(self):
        self.sent.append(("disconnect",))

    async def reply(self, frame, body):
        self.sent.append(("reply", frame["headers"]["req_id"], body))

    async def reply_stream(self, frame, stream_id, content, finish=False):
        if self.expire_streams and finish:
            raise RuntimeError("Reply ack error: errcode=846608, errmsg=stream expired")
        self.sent.append(("stream", stream_id, content, finish))

    async def send_message(self, chatid, body):
        self.sent.append(("push", chatid, body))


def frame(body, req="req-1"):
    return {"cmd": "aibot_msg_callback", "headers": {"req_id": req}, "body": body}


async def started():
    received, statuses = [], []

    async def on_message(message, connection):
        received.append(message)

    async def on_status(status, error):
        statuses.append((status, error))

    connection = WecomConnection(channel_id="ch1", workspace_id="ws1", bot_id="bot-1", secret="s", bot_name="行政助手",
                                 on_message=on_message, on_status=on_status, client_factory=FakeClient)
    await connection.start()
    await asyncio.sleep(0)
    return connection, received, statuses


def test_mentions_are_stripped():
    assert strip_mention("@行政助手 报销标准", "行政助手") == "报销标准"
    assert strip_mention("@某机器人 报销标准", "") == "报销标准"
    assert strip_mention("报销标准", "行政助手") == "报销标准"


async def test_frames_become_messages_and_status():
    connection, received, statuses = await started()
    client = connection.client
    assert client.options.max_reconnect_attempts == -1
    client.handlers["message.text"](frame({"msgid": "m1", "chattype": "single", "from": {"userid": "zhangsan"},
                                           "msgtype": "text", "text": {"content": "报销标准"}}))
    client.handlers["message.text"](frame({"msgid": "m2", "chattype": "group", "chatid": "g1",
                                           "from": {"userid": "lisi"}, "msgtype": "text",
                                           "text": {"content": "@行政助手 出差住宿"}}))
    client.handlers["message.mixed"](frame({"msgid": "m3", "chattype": "single", "from": {"userid": "wangwu"},
                                            "msgtype": "mixed", "mixed": {"msg_item": [
                                                {"msgtype": "text", "text": {"content": "这张图"}},
                                                {"msgtype": "image", "image": {"url": "https://x"}}]}}))
    client.handlers["message.voice"](frame({"msgid": "m4", "chattype": "single", "from": {"userid": "zhaoliu"},
                                            "msgtype": "voice", "voice": {"content": "语音转的字"}}))
    await asyncio.sleep(0)
    assert [(m.external_id, m.chat_type, m.chat_id, m.text, m.session_key) for m in received] == [
        ("zhangsan", "direct", "", "报销标准", "dm:zhangsan"),
        ("lisi", "group", "g1", "出差住宿", "group:g1:lisi"),
        ("wangwu", "direct", "", "这张图", "dm:wangwu"),
        ("zhaoliu", "direct", "", "语音转的字", "dm:zhaoliu"),
    ]
    client.handlers["event.disconnected_event"]({})
    client.handlers["error"](Exception("Authentication failed: invalid secret (code: 40001)"))
    client.handlers["error"](Exception("Authentication failed: invalid bot_id or secret (code: 853000)"))
    await asyncio.sleep(0)
    assert statuses[0] == ("connecting", "")
    assert statuses[1] == ("connected", "")
    assert "在别处建立了新连接" in statuses[2][1]
    assert statuses[3][0] == "error" and "认证失败" in statuses[3][1]
    # 测试里的 Bot ID 不是纯数字：提示核对长连接、最新 Secret，不提 AgentId
    assert "使用长连接" in statuses[4][1] and "AgentId" not in statuses[4][1]
    assert "AgentId" in wecom_reason("invalid bot_id or secret (code: 853000)", "1000006")
    await connection.stop()
    assert client.sent[-1] == ("disconnect",)


async def test_streams_fall_back_to_a_push_when_expired():
    connection, received, _ = await started()
    client = connection.client
    client.handlers["message.text"](frame({"msgid": "m1", "chattype": "group", "chatid": "g1",
                                           "from": {"userid": "lisi"}, "msgtype": "text",
                                           "text": {"content": "@行政助手 问题"}}))
    await asyncio.sleep(0)
    message = received[0]
    stream = await connection.stream(message, "正在思考…")
    await stream.update("一半")
    await stream.finish("完整回答")
    kinds = [(s[0], s[2], s[3]) for s in client.sent if s[0] == "stream"]
    assert kinds == [("stream", "正在思考…", False), ("stream", "一半", False), ("stream", "完整回答", True)]

    client.expire_streams = True
    expired = await connection.stream(message, "正在思考…")
    await expired.finish("迟到的回答")
    assert client.sent[-1] == ("push", "g1", {"msgtype": "markdown", "markdown": {"content": "迟到的回答"}})
    await connection.reply(message, "你好")
    assert client.sent[-1] == ("reply", "req-1", {"msgtype": "markdown", "markdown": {"content": "你好"}})


async def test_pictures_are_uploaded_then_sent_as_image_messages():
    connection, received, _ = await started()
    client = connection.client
    client.handlers["message.text"](frame({"msgid": "m1", "chattype": "single", "from": {"userid": "lisi"},
                                           "msgtype": "text", "text": {"content": "问题"}}))
    await asyncio.sleep(0)
    stream = await connection.stream(received[0], "正在思考…")
    big = b"j" * (512 * 1024 + 10)  # 两片
    await stream.finish("看图", [Picture(data=b"png-bytes", mime="image/png"), Picture(data=b"gif", mime="image/gif"),
                                 Picture(data=big, mime="image/jpeg")])
    frames = client._ws_manager.frames
    # 只收 jpg、png；每张三步：init（带 md5 与分片数）→ 分片 → finish
    assert [cmd.removeprefix("aibot_upload_media_") for cmd, _ in frames] == [
        "init", "chunk", "finish", "init", "chunk", "chunk", "finish"]
    assert frames[0][1] == {"type": "image", "filename": "image.png", "total_size": 9, "total_chunks": 1,
                            "md5": hashlib.md5(b"png-bytes").hexdigest()}
    assert frames[1][1]["base64_data"] == base64.b64encode(b"png-bytes").decode()
    assert frames[3][1]["total_chunks"] == 2
    # 文字先发完，再挂在收到的那一帧上各回一条图片消息
    assert [s[0] for s in client.sent][-3:] == ["stream", "reply", "reply"]
    assert client.sent[-2] == ("reply", "req-1", {"msgtype": "image", "image": {"media_id": "media-up1"}})

    client.expire_streams = True  # 流式过期：文字与图都改成主动推
    await (await connection.stream(received[0], "正在思考…")).finish("迟到", [Picture(data=b"p", mime="image/png")])
    assert client.sent[-1] == ("push", "lisi", {"msgtype": "image", "image": {"media_id": "media-up8"}})
