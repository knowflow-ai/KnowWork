"""渠道测试的共用部分：真实的库与权限，假的会话服务（按脚本吐事件）与假的平台连接（记下回了什么）。"""

import asyncio
import itertools
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from knowwork_server.access.service import AccessService
from knowwork_server.channels import build_channels
from knowwork_server.channels.models import Channel
from knowwork_server.conversations.models import Conversation
from knowwork_server.conversations.replay import Outgoing
from knowwork_server.conversations.service import ConversationNotFound
from knowwork_server.db.base import make_engine, make_sessionmaker, make_workspace_sessionmaker
from knowwork_server.identity.users import UserService

MASTER = "test-master-key-for-channels-0123456789"
SEQ = itertools.count(1)


def run(turn: int, *events: tuple[str, dict]) -> list[Outgoing]:
    """一轮的事件：run.started 开头，后面按给的 (类型, 载荷) 排。"""
    run_id = f"run-{turn}"
    items = [Outgoing(sse_id=str(next(SEQ)), type="run.started", payload={"type": "run.started", "run_id": run_id})]
    for kind, payload in events:
        items.append(Outgoing(sse_id=None if kind == "live" else str(next(SEQ)), type=kind,
                              payload={"run_id": run_id, **payload}))
    return items


def answer(turn: int, text: str) -> list[Outgoing]:
    return run(turn, ("live", {"step": 1, "offset": 0, "text": text[:3]}),
               ("item.completed", {"kind": "message", "data": {"role": "assistant", "text": text}}),
               ("run.completed", {}))


@dataclass
class FakeConversations:
    sessionmaker: object
    scripts: list[list[Outgoing]] = field(default_factory=list)
    created: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    gone: set = field(default_factory=set)
    cancelled: list = field(default_factory=list)

    async def create(self, *, workspace_id, user_id, title="", selection=None):
        conversation = SimpleNamespace(id=f"c_{uuid.uuid4().hex}", workspace_id=workspace_id, user_id=user_id)
        async with self.sessionmaker() as session, session.begin():
            session.add(Conversation(id=conversation.id, workspace_id=workspace_id, user_id=user_id, title=title))
        self.created.append((conversation, title, selection))
        return conversation

    async def get(self, *, conversation_id, workspace_id, user_id):
        if conversation_id in self.gone:
            raise ConversationNotFound(conversation_id)
        return SimpleNamespace(id=conversation_id, workspace_id=workspace_id, user_id=user_id)

    async def send(self, conversation, turn, selection=None):
        self.sent.append((conversation.id, turn, selection))
        return "msg-1"

    async def events(self, conversation, cursor):
        for item in self.scripts.pop(0) if self.scripts else []:
            if item.type == "hang":
                await asyncio.sleep(3600)
            yield item

    async def cancel(self, conversation):
        self.cancelled.append(conversation.id)


@dataclass
class FakeStream:
    placeholder: str
    updates: list = field(default_factory=list)
    final: str | None = None

    async def update(self, text):
        self.updates.append(text)

    async def finish(self, text, pictures=()):
        self.final = text
        self.pictures = list(pictures)


@dataclass
class FakeConnection:
    replies: list = field(default_factory=list)
    streams: list = field(default_factory=list)

    async def reply(self, message, text):
        self.replies.append(text)

    async def stream(self, message, placeholder):
        stream = FakeStream(placeholder)
        self.streams.append(stream)
        return stream


@dataclass
class FakePlatform:
    """假的平台连接工厂：记下起停了哪些渠道。"""

    started: list = field(default_factory=list)
    stopped: list = field(default_factory=list)

    def __call__(self, channel, secrets, on_message, on_status):
        platform = self

        class Connection:
            async def start(self):
                platform.started.append((channel.id, secrets.get("secret")))
                await on_status("connected", "")

            async def stop(self):
                platform.stopped.append(channel.id)

        return Connection()


@pytest.fixture
async def env(database_url):
    engine = make_engine(database_url)
    from knowwork_server.db.base import Base
    from knowwork_server.db.registry import load_all_models
    load_all_models()
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    global_sessionmaker = make_sessionmaker(engine)
    scoped = make_workspace_sessionmaker(engine)
    ws = "ws_" + uuid.uuid4().hex[:10]
    access = AccessService(global_sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id=ws, name="渠道", owner_user_id="boss")
    await access.add_member("boss", ws, "alice", "editor")
    conversations = FakeConversations(scoped)
    platform = FakePlatform()
    users = UserService(global_sessionmaker)
    channels = build_channels(MASTER, scoped, global_sessionmaker, None, access, conversations,
                              accounts=users, factories={"wecom": platform})
    yield SimpleNamespace(ws=ws, access=access, conversations=conversations, platform=platform, users=users,
                          channels=channels, scoped=scoped, engine=engine, global_sessionmaker=global_sessionmaker)
    await channels.supervisor.stop()
    await engine.dispose()


async def make_channel(env, **settings) -> Channel:
    settings = {"bases": ["builtin.kb1"], **settings}
    from knowwork_server.channels.admin import ChannelInput
    from knowwork_server.channels.settings import ChannelSettings
    from knowwork_server.db.base import in_workspace
    with in_workspace(env.ws):
        view = await env.channels.admin.create("boss", env.ws, ChannelInput(
            name="行政助手", platform="wecom", enabled=True, bot_id=f"bot-{uuid.uuid4().hex[:6]}",
            bot_name="行政助手", secret="sk-wecom-secret-123456", settings=ChannelSettings(**settings)))
        return await env.channels.admin._store.get(env.ws, view.id)
