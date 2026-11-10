"""渠道的回答方式：除了内置两种（查知识库），扩展登记、写了查哪类资源的也能选（例如问数查数据源）。

管理：下拉里只有写了资源类型、配渠道的人能用的；选了查数据源的，群聊（不认人时私聊也是）可查的数据源同步成机器人账号的
「使用」授权，知识库那边清掉，换回来再反过来；不认人至少选一个。回答：照选的回答方式发；认出的人用不了这种回答方式时
说清楚找谁，群里没选资源时按资源的名字说。"""

import pytest

from knowwork_server.channels.admin import ChannelInput, Invalid
from knowwork_server.channels.settings import ChannelSettings
from knowwork_server.db.base import in_workspace

from .conftest import FakeConnection, answer, make_channel
from .test_people import member_channel, message, person

pytestmark = pytest.mark.anyio


def _input(**settings) -> ChannelInput:
    return ChannelInput(name="数据助手", platform="wecom", enabled=True, bot_id="bot-numbers", bot_name="数据助手",
                        secret="sk-secret-123456", settings=ChannelSettings(**settings))


async def grants(env, account):
    found = await env.access.list_grants("boss", env.ws, subject=f"user:{account}")
    return sorted(grant.resource for grant in found)


async def test_admins_pick_from_modes_that_name_their_resource(env):
    with in_workspace(env.ws):
        modes = await env.channels.admin.answer_modes("boss", env.ws)
    assert [(m.id, m.label, m.resource_type, m.resource_label) for m in modes] == [
        ("agent", "智能体", "knowledge_base", "知识库"), ("quick", "快速问答", "knowledge_base", "知识库"),
        ("numbers", "问数", "data_source", "数据源")]


async def test_a_data_source_channel_grants_its_account_the_chosen_sources(env):
    admin = env.channels.admin
    with in_workspace(env.ws):
        for data, error in [
            (_input(answer_mode="modeling", bases=["builtin.kb1"]), "这种回答方式在渠道里用不了，请重新选"),
            (_input(answer_mode="numbers", bases=["builtin.kb1"]), "至少选一个数据源：机器人只按选的数据源回答"),
        ]:
            with pytest.raises(Invalid, match=error):
                await admin.create("boss", env.ws, data)
        view = await admin.create("boss", env.ws, _input(answer_mode="numbers", bases=["builtin.kb1"],
                                                         resources=["ds_sales", "ds_hr"]))
        # 查数据源的渠道不留知识库：存下来的配置和授权都只有数据源
        assert (view.settings["bases"], view.settings["resources"]) == ([], ["ds_sales", "ds_hr"])
        assert await grants(env, view.id) == ["data_source:ds_hr", "data_source:ds_sales"]

        view = await admin.update("boss", env.ws, view.id, _input(answer_mode="numbers", resources=["ds_sales"]))
        assert await grants(env, view.id) == ["data_source:ds_sales"]
        # 换回查知识库：数据源的授权收回
        view = await admin.update("boss", env.ws, view.id, _input(answer_mode="agent", bases=["builtin.kb2"],
                                                                  resources=["ds_sales"]))
        assert view.settings["resources"] == []
        assert await grants(env, view.id) == ["knowledge_base:builtin.kb2"]


async def test_the_channel_account_answers_with_the_chosen_mode(env):
    channel = await make_channel(env, answer_mode="numbers", bases=[], resources=["ds_sales"])
    env.conversations.scripts = [answer(1, "上个月销售额 120 万。")]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, "上个月销售额多少"), connection)
    [(_, turn, _)] = env.conversations.sent
    assert turn.answer_mode == "numbers"
    assert connection.streams[0].final == "上个月销售额 120 万。"


async def test_people_who_cannot_use_the_mode_are_told_whom_to_ask(env):
    channel = await member_channel(env, answer_mode="numbers")
    await person(env, "u-zhang", "zhangsan")  # 认得出，但没有任何数据源的授权
    connection = FakeConnection()
    await env.channels.service.handle(message(channel), connection)
    assert connection.replies[-1] == "你还没有可查的数据源，请联系管理员授权。"
    assert env.conversations.created == []  # 先看能不能用再建会话：不在他的「最近」里留一个空对话

    # 群里没选数据源：按资源的名字说
    await env.channels.service.handle(message(channel, "群里问", group="chat-1"), connection)
    assert connection.replies[-1] == "这个机器人在群里没有可查的数据源。要按你自己的权限查，请私聊我。"
