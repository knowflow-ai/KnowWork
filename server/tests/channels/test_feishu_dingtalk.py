"""飞书与钉钉适配器（官方 SDK 换成假的）：按线程取的事件循环代理让多个飞书机器人各跑各的循环；消息从 SDK 的线程
转交回主循环；回复与流式走对线程；钉钉卡片发不出去时退回普通回复；停下时机器人线程能退出。"""

import asyncio
import json
import threading
from types import SimpleNamespace

import httpx
import lark_oapi.ws.client as ws_client_module
import pytest
from dingtalk_stream import ChatbotMessage

from knowwork_server.channels import dingtalk, feishu
from knowwork_server.channels.dingtalk import DingtalkConnection
from knowwork_server.channels.feishu import FeishuConnection
from knowwork_server.channels.types import Picture

pytestmark = pytest.mark.anyio


async def wait_for(predicate, timeout=5.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.02)


def recorder():
    received, statuses = [], []

    async def on_message(message, connection):
        received.append((message, threading.current_thread() is threading.main_thread()))

    async def on_status(status, error):
        statuses.append((status, error))

    return received, statuses, on_message, on_status


# ---- 飞书 ----

def test_each_feishu_thread_runs_its_own_sdk_loop():
    feishu._install_thread_loop()
    used, errors = [], []

    def run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def work():
                await asyncio.sleep(0.1)
                used.append(asyncio.get_running_loop())
            ws_client_module.loop.run_until_complete(work())  # SDK 里就是这么用模块级 loop 的
        except RuntimeError as error:  # 共用一个循环时第二个线程会报「This event loop is already running」
            errors.append(error)
        finally:
            loop.close()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == [] and len({id(loop) for loop in used}) == 2


class FakeFeishuChannel:
    """模仿 FeishuChannel：start() 阻塞跑 SDK 的模块级循环；处理函数在它自己的后台线程里调；schedule 把协程放到后台循环。"""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.handlers = {}
        self.sent, self.contents = [], []
        self.ws_client = None
        self.stopped_in = None
        self._bg = asyncio.new_event_loop()
        threading.Thread(target=self._bg.run_forever, daemon=True).start()

    def on(self, name, handler):
        self.handlers[name] = handler

    def start(self):
        async def disconnect():
            self.ws_client._conn = None

        self.ws_client = SimpleNamespace(_conn=object(), _auto_reconnect=True, _disconnect=disconnect)

        async def forever():
            while True:
                await asyncio.sleep(3600)

        ws_client_module.loop.run_until_complete(forever())

    def stop(self):
        self.stopped_in = threading.current_thread().name
        self._bg.call_soon_threadsafe(self._bg.stop)

    def schedule(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self._bg)

    def deliver(self, inbound):
        self._bg.call_soon_threadsafe(self.handlers["message"], inbound)

    async def send(self, to, message, opts=None):
        self.sent.append((to, message, opts))

    async def stream(self, to, spec, opts=None):
        controller = SimpleNamespace(set_content=self._set_content)
        await spec["markdown"](controller)
        self.sent.append((to, "stream", opts))

    async def _set_content(self, text):
        self.contents.append(text)

    async def upload_media(self, source, *, kind):
        if source.buffer == b"broken":
            raise RuntimeError("upload failed")
        return f"img_{len(source.buffer)}"


def inbound(text, *, chat_type="p2p", chat_id="oc_1", sender="ou_zhangsan", bot=False, mid="om_1"):
    return SimpleNamespace(id=mid, content_text=text,
                           conversation=SimpleNamespace(chat_id=chat_id, chat_type=chat_type),
                           sender=SimpleNamespace(open_id=sender, display_name="张三", is_bot=bot))


async def test_feishu_messages_replies_and_streams():
    received, statuses, on_message, on_status = recorder()
    connection = FeishuConnection(channel_id="ch1", workspace_id="ws1", app_id="cli_x", secret="s",
                                  on_message=on_message, on_status=on_status, channel_factory=FakeFeishuChannel)
    feishu.WATCH_SECONDS = 0.05
    await connection.start()
    channel = connection.channel
    await wait_for(lambda: ("connected", "") in statuses)
    assert channel.kwargs["app_id"] == "cli_x"

    channel.deliver(inbound("报销标准"))
    channel.deliver(inbound("@_user_1 出差住宿", chat_type="group", chat_id="oc_g", mid="om_2"))
    channel.deliver(inbound("机器人自己发的", sender="ou_bot", bot=True, mid="om_3"))
    await wait_for(lambda: len(received) == 2)
    assert [(m.external_id, m.chat_type, m.chat_id, m.text, main) for m, main in received] == [
        ("ou_zhangsan", "direct", "oc_1", "报销标准", True),
        ("ou_zhangsan", "group", "oc_g", "出差住宿", True),
    ]
    message = received[0][0]
    await connection.reply(message, "你好")
    assert channel.sent[-1] == ("oc_1", {"markdown": "你好"}, {"reply_to": "om_1"})
    stream = await connection.stream(message, "正在思考…")
    await stream.update("一半")
    await stream.finish("完整回答")
    assert channel.contents == ["正在思考…", "一半", "完整回答"]
    assert channel.sent[-1] == ("oc_1", "stream", {"reply_to": "om_1"})
    # 回答里的图传上去换 image_key，接在回答后面；传不上去的跳过
    pictured = await connection.stream(message, "正在思考…")
    await pictured.finish("看图", [Picture(data=b"abc", mime="image/png", caption="图 [1] 流程"),
                                   Picture(data=b"broken", mime="image/png")])
    assert channel.contents[-1] == "看图\n\n![图 1 流程](img_3)"

    await connection.stop()
    assert channel.stopped_in == "feishu-ch1"  # SDK 的收尾在机器人自己的线程里调
    assert not connection._thread.is_alive()


# ---- 钉钉 ----

class FakeDingtalkClient:
    def __init__(self, credential, logger=None):
        self.credential = credential
        self.handlers = {}
        self.websocket = None
        self.valid = credential.client_secret == "good"
        self.uploaded = []

    def upload_to_dingtalk(self, data, filetype="image", filename="image.png", mimetype="image/png"):
        self.uploaded.append((filename, mimetype))
        return f"@media{len(self.uploaded)}"

    def get_access_token(self):
        return "token-1"

    def register_callback_handler(self, topic, handler):
        self.handlers[topic] = handler

    def open_connection(self):
        return {"endpoint": "wss://x", "ticket": "t"} if self.valid else None

    async def start(self):
        self.websocket = SimpleNamespace(state=SimpleNamespace(name="OPEN"), close=self._close)
        while True:
            await asyncio.sleep(3600)

    async def _close(self):
        self.websocket.state = SimpleNamespace(name="CLOSED")


class FakeCard:
    def __init__(self, client, incoming, works=True):
        self.card_instance_id = None
        self.works = works
        self.calls = []

    def ai_start(self):
        self.card_instance_id = "card-1" if self.works else ""

    def ai_streaming(self, text, append):
        self.calls.append(("stream", text))

    def ai_finish(self, text):
        self.calls.append(("finish", text))


def chatbot(text, *, group=False):
    return ChatbotMessage.from_dict({
        "msgId": "msg1", "senderStaffId": "manager01", "senderId": "$:enc", "senderNick": "王经理",
        "conversationType": "2" if group else "1", "conversationId": "cid-1", "msgtype": "text",
        "text": {"content": text}, "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?x"})


async def test_dingtalk_messages_cards_and_fallback(monkeypatch):
    received, statuses, on_message, on_status = recorder()
    cards = []

    def card_factory(client, incoming):
        card = FakeCard(client, incoming, works=len(cards) == 0)
        cards.append(card)
        return card

    dingtalk.WATCH_SECONDS = 0.05
    connection = DingtalkConnection(channel_id="ch2", workspace_id="ws1", client_id="ding_x", secret="good",
                                    on_message=on_message, on_status=on_status,
                                    client_factory=FakeDingtalkClient, card_factory=card_factory)
    replies = []
    connection._replier = SimpleNamespace(reply_markdown=lambda title, text, raw: replies.append(text) or {"ok": 1})
    await connection.start()
    await wait_for(lambda: ("connected", "") in statuses)

    handler = connection.client.handlers[ChatbotMessage.TOPIC]
    await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(
        handler.process(SimpleNamespace(data=chatbot(" 报销标准", group=True).to_dict())), connection._thread_loop))
    await wait_for(lambda: len(received) == 1)
    message, on_main = received[0]
    assert (message.external_id, message.chat_type, message.chat_id, message.text, on_main) == (
        "manager01", "group", "cid-1", "报销标准", True)

    stream = await connection.stream(message, "正在思考…")
    await stream.update("一半")
    await stream.finish("完整回答")
    assert cards[0].calls == [("stream", "正在思考…"), ("stream", "一半"), ("finish", "完整回答")]
    # 第二张卡片发不出去：退回会话 webhook 回一条
    fallback = await connection.stream(message, "正在思考…")
    await fallback.finish("降级的回答")
    assert replies == ["降级的回答"]

    # 回答里的图：上传换 mediaId，再单独发图片消息；群里按会话 id 发，格式不认识的跳过
    posted = []

    def fake_post(url, **kwargs):
        posted.append((url, kwargs["json"], kwargs["headers"]["x-acs-dingtalk-access-token"]))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(dingtalk.httpx, "post", fake_post)
    with_pictures = await connection.stream(message, "正在思考…")
    await with_pictures.finish("看图", [Picture(data=b"a", mime="image/png"), Picture(data=b"b", mime="image/webp")])
    assert connection.client.uploaded == [("image.png", "image/png")]
    [(url, body, token)] = posted
    assert url.endswith("/robot/groupMessages/send") and token == "token-1"
    assert (body["openConversationId"], body["msgKey"], json.loads(body["msgParam"])) == (
        "cid-1", "sampleImageMsg", {"photoURL": "@media1"})

    await connection.stop()
    assert not connection._thread.is_alive()


async def test_dingtalk_bad_credentials_show_up_as_an_error():
    _, statuses, on_message, on_status = recorder()
    connection = DingtalkConnection(channel_id="ch3", workspace_id="ws1", client_id="ding_x", secret="bad",
                                    on_message=on_message, on_status=on_status, client_factory=FakeDingtalkClient)
    await connection.start()
    assert statuses[-1][0] == "error" and "Client ID" in statuses[-1][1]
    await connection.stop()
