"""运行管理：启动时连上启用的渠道；同一个机器人配在两个空间（或起了两份业务服务）只有一份连得上，另一份标成
出错、等锁；先连上的停用了，锁就放开。"""

import uuid

import pytest

from knowwork_server.channels import build_channels
from knowwork_server.channels.admin import ChannelInput
from knowwork_server.channels.settings import ChannelSettings
from knowwork_server.db.base import in_workspace

from .conftest import MASTER, FakeConversations, FakePlatform

pytestmark = pytest.mark.anyio


async def test_one_bot_is_connected_once(env):
    other = "ws_" + uuid.uuid4().hex[:10]
    await env.access.create_workspace(workspace_id=other, name="另一个", owner_user_id="boss")
    data = ChannelInput(name="助手", platform="wecom", enabled=True, bot_id="bot-shared", bot_name="",
                        secret="sk-secret-0000-1111", settings=ChannelSettings(bases=["builtin.kb1"]))

    def supervisor_with(platform):
        return build_channels(MASTER, env.scoped, env.global_sessionmaker, env.engine, env.access,
                              FakeConversations(env.scoped), accounts=env.users, factories={"wecom": platform})

    first_platform, second_platform = FakePlatform(), FakePlatform()
    first, second = supervisor_with(first_platform), supervisor_with(second_platform)
    with in_workspace(env.ws):
        mine = await first.admin.create("boss", env.ws, data)
    with in_workspace(other):
        theirs = await second.admin.create("boss", other, data)  # 别的空间查不到重名，靠锁兜底
    assert [started[0] for started in first_platform.started] == [mine.id]
    assert second_platform.started == []
    with in_workspace(other):
        blocked = await second.admin._store.get(other, theirs.id)
    assert blocked.status == "error" and "另一个" in blocked.last_error

    with in_workspace(env.ws):
        await first.admin.set_enabled("boss", env.ws, mine.id, False)  # 停用：断开并放开锁
    second_fresh = supervisor_with(second_platform)
    await second_fresh.supervisor.start()  # 另一份现在能连上（库里别的测试留下的启用渠道也会一起连上）
    assert theirs.id in [started[0] for started in second_platform.started]
    await first.supervisor.stop()
    await second.supervisor.stop()
    await second_fresh.supervisor.stop()
