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
from knowwork_server.channels.targets import ChannelTargets, Target
from knowwork_server.conversations.answer_modes import (
    AnswerMode,
    AnswerModes,
    ChannelResource,
    ModeContext,
    UnknownAnswerMode,
)
from knowwork_server.conversations.models import Conversation
from knowwork_server.conversations.replay import Outgoing
from knowwork_server.conversations.service import ConversationNotFound
from knowwork_server.db.base import make_engine, make_sessionmaker, make_workspace_sessionmaker
from knowwork_server.identity.im_identities import ImIdentities, ImIdentitySource
from knowwork_server.identity.users import UserService
from knowwork_server.kernel.contract import KernelError

MASTER = "test-master-key-for-channels-0123456789"


@dataclass
class FakeTargets:
    """扩展登记的可挂对象（例如专家）：测试里直接改 items，挂上的会话记在 attached。"""

    kind: str = "expert"
    label: str = "专家"
    items: dict[str, Target] = field(default_factory=dict)
    attached: list[tuple[str, str, str]] = field(default_factory=list)

    async def list(self, workspace_id: str) -> list[Target]:
        return list(self.items.values())

    async def get(self, workspace_id: str, target_id: str) -> Target | None:
        return self.items.get(target_id)

    async def attach(self, workspace_id: str, conversation_id: str, target_id: str, user_id: str) -> None:
        self.attached.append((conversation_id, target_id, user_id))
BASE_URL = "https://kw.example.com"
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
    modes: AnswerModes = field(default_factory=AnswerModes)
    scripts: list[list[Outgoing]] = field(default_factory=list)
    created: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    gone: set = field(default_factory=set)
    cancelled: list = field(default_factory=list)
    pending: list = field(default_factory=list)  # 等着回答的提问（kernel.contract.PendingQuestion）
    approvals_waiting: list = field(default_factory=list)  # 等着批准的操作
    asks: dict = field(default_factory=dict)  # 事件流走到这个 id 的 run.waiting 时，这次提问开始等回答
    answered: list = field(default_factory=list)

    async def create(self, *, workspace_id, user_id, title="", selection=None, channel_id=None, source=""):
        conversation = SimpleNamespace(id=f"c_{uuid.uuid4().hex}", workspace_id=workspace_id, user_id=user_id,
                                       channel_id=channel_id, source=source)
        async with self.sessionmaker() as session, session.begin():
            session.add(Conversation(id=conversation.id, workspace_id=workspace_id, user_id=user_id, title=title,
                                     channel_id=channel_id, source=source))
        self.created.append((conversation, title, selection))
        return conversation

    async def get(self, *, conversation_id, workspace_id, user_id):
        if conversation_id in self.gone:
            raise ConversationNotFound(conversation_id)
        return SimpleNamespace(id=conversation_id, workspace_id=workspace_id, user_id=user_id)

    async def send(self, conversation, turn, selection=None):
        # 同 ConversationService：这个人此刻用不了这种回答方式就发不出去
        offered = await self.modes.for_user(ModeContext(conversation.workspace_id, conversation.user_id))
        if turn.answer_mode not in {mode.id for mode in offered}:
            raise UnknownAnswerMode(turn.answer_mode)
        self.sent.append((conversation.id, turn, selection))
        return "msg-1"

    async def events(self, conversation, cursor):
        for item in self.scripts.pop(0) if self.scripts else []:
            if item.type == "hang":
                await asyncio.sleep(3600)
            if item.type == "run.waiting" and item.payload.get("item_id") in self.asks:
                self.pending.append(self.asks[item.payload["item_id"]])
            yield item

    async def cancel(self, conversation):
        self.cancelled.append(conversation.id)

    async def questions(self, conversation):
        return list(self.pending)

    async def approvals(self, conversation):
        return list(self.approvals_waiting)

    async def answer(self, conversation, question_id, answer):
        if all(q.id != question_id for q in self.pending):
            raise KernelError("question_not_found", "这个问题已经结束", 404)
        self.pending = [q for q in self.pending if q.id != question_id]
        self.answered.append((conversation.id, question_id, answer))


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
    access.add_resource_types(frozenset({"data_source"}))  # 扩展登记的资源类型（例如问数的数据源）
    assert await access.reload()
    await access.create_workspace(workspace_id=ws, name="渠道", owner_user_id="boss")
    await access.add_member("boss", ws, "alice", "editor")
    # 扩展登记的回答方式：查数据源，有数据源授权的人（和空间管理员）才能用
    async def numbers_available(ctx):
        if access.check(ctx.user_id, ctx.workspace_id, "workspace", "edit"):
            return True
        grants = await access.list_grants("boss", ctx.workspace_id, subject=f"user:{ctx.user_id}")
        return any(grant.resource.startswith("data_source:") for grant in grants)

    modes = AnswerModes()
    modes.add(AnswerMode("numbers", "问数", "查数据库里的数", channel=ChannelResource("data_source", "数据源"),
                         available=numbers_available))
    modes.add(AnswerMode("modeling", "建模", "改语义模型"))  # 没写查哪类资源：不进渠道
    conversations = FakeConversations(scoped, modes)
    platform = FakePlatform()
    users = UserService(global_sessionmaker)
    identities = ImIdentities()  # 测试里登记的组织来源（扩展登记的那种）
    sources: list[ImIdentitySource] = []
    identities.add(lambda: list(sources))
    targets, experts = ChannelTargets(), FakeTargets()  # 扩展登记的可挂对象（例如专家）
    targets.add(experts)
    channels = build_channels(MASTER, scoped, global_sessionmaker, None, access, conversations,
                              accounts=users, factories={"wecom": platform}, identities=identities,
                              public_base_url=BASE_URL, answer_modes=modes, targets=targets)
    yield SimpleNamespace(ws=ws, access=access, conversations=conversations, platform=platform, users=users,
                          channels=channels, scoped=scoped, engine=engine, global_sessionmaker=global_sessionmaker,
                          sources=sources, targets=targets, experts=experts)
    await channels.supervisor.stop()
    # 删掉这个测试建的渠道：supervisor 的测试会连上库里全部启用的渠道（每个占一条连接拿锁），留多了会把连接池耗尽
    from knowwork_server.db.base import in_workspace
    with in_workspace(ws):
        for channel in await channels.admin._store.of_workspace(ws):
            await channels.admin._store.delete(ws, channel.id)
    await engine.dispose()


async def make_channel(env, *, audience="public", **settings) -> Channel:
    settings = {"bases": ["builtin.kb1"], **settings}
    from knowwork_server.channels.admin import ChannelInput
    from knowwork_server.channels.settings import ChannelSettings
    from knowwork_server.db.base import in_workspace
    with in_workspace(env.ws):
        view = await env.channels.admin.create("boss", env.ws, ChannelInput(
            name="行政助手", platform="wecom", enabled=True, bot_id=f"bot-{uuid.uuid4().hex[:6]}",
            bot_name="行政助手", secret="sk-wecom-secret-123456", settings=ChannelSettings(**settings),
            audience=audience))
        return await env.channels.admin._store.get(env.ws, view.id)
