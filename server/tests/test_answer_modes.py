"""回答方式登记表：内置两种、扩展登记的校验、按人列出、发消息时现算的内容与出错时的处理。"""

import pytest

from knowwork_server.conversations.answer_modes import (
    KNOWLEDGE,
    AnswerMode,
    AnswerModes,
    AnswerModeUnavailable,
    ChannelResource,
    ModeContext,
    UnknownAnswerMode,
)
from knowwork_server.tools.registry import ToolSpec

pytestmark = pytest.mark.anyio
CTX = ModeContext(workspace_id="ws", user_id="u1", conversation_id="c1")


async def test_built_ins_are_listed_and_carry_nothing():
    modes = AnswerModes()
    assert [m.id for m in await modes.for_user(CTX)] == ["agent", "quick"]
    assert await modes.spec("agent", CTX) is None and await modes.spec("quick", CTX) is None
    with pytest.raises(UnknownAnswerMode):
        await modes.spec("custom", CTX)


def test_registration_is_validated():
    modes = AnswerModes()
    for bad in (AnswerMode("Custom", "扩展方式", ""), AnswerMode("agent", "智能体", ""),
                AnswerMode("custom", "", ""), AnswerMode("custom", "扩展方式", "", official_tools=("bad name",))):
        with pytest.raises(ValueError):
            modes.add(bad)
    modes.add(AnswerMode("custom", "扩展方式", "扩展登记的一种"))
    with pytest.raises(ValueError):
        modes.add(AnswerMode("custom", "扩展方式", "重复登记"))


async def test_availability_and_spec():
    async def available(ctx):
        if ctx.user_id == "broken":
            raise RuntimeError("lookup failed")
        return ctx.user_id != "outsider"

    async def prompt(ctx):
        if ctx.conversation_id == "boom":
            raise RuntimeError("summary failed")
        return "规" * 30_000

    modes = AnswerModes()
    modes.add(AnswerMode("custom", "扩展方式", "扩展登记的一种", official_tools=("todo_write", "skill", "todo_write"),
                         prompt=prompt, available=available))
    assert [m.id for m in await modes.for_user(CTX)] == ["agent", "quick", "custom"]
    for user in ("outsider", "broken"):  # 判断出错时按不能用
        assert [m.id for m in await modes.for_user(ModeContext("ws", user))] == ["agent", "quick"]
        with pytest.raises(UnknownAnswerMode):
            await modes.spec("custom", ModeContext("ws", user, "c1"))
    spec = await modes.spec("custom", CTX)
    assert spec.tools == ("skill", "todo_write") and len(spec.prompt) == 20_000, "去重排序；指令截到上限"
    with pytest.raises(AnswerModeUnavailable):
        await modes.spec("custom", ModeContext("ws", "u1", "boom"))


def test_tools_may_declare_registered_modes():
    spec = ToolSpec("ext_lookup", "扩展的查询工具", {"type": "object", "properties": {}},
                    answer_modes=frozenset({"agent", "custom"}))
    assert spec.describe()["answerModes"] == ["agent", "custom"]
    with pytest.raises(ValueError):
        ToolSpec("x", "x", {"type": "object", "properties": {}}, answer_modes=frozenset({"Bad mode"}))


async def test_unlisted_modes_stay_out_of_the_menu_but_still_work():
    """只给后台对话用的回答方式（listed=False）：下拉里没有，发消息时照样认（后台起草就是这样发的）。"""
    async def prompt(ctx):
        return "起草规则"

    modes = AnswerModes()
    modes.add(AnswerMode("drafting", "起草", "后台起草用", prompt=prompt, listed=False))
    assert [m.id for m in await modes.for_user(CTX)] == ["agent", "quick"]
    assert (await modes.spec("drafting", CTX)).prompt == "起草规则"


async def test_only_modes_that_name_their_resource_go_into_channels():
    # IM 渠道里不认人或群聊时用机器人账号回答，要知道按哪类资源给它授权；没写的（改东西的建模这类）不进渠道
    async def available(ctx):
        return ctx.user_id != "outsider"

    modes = AnswerModes()
    modes.add(AnswerMode("numbers", "问数", "查数", channel=ChannelResource("data_source", "数据源"),
                         available=available))
    modes.add(AnswerMode("modeling", "建模", "改模型"))
    assert [(m.id, m.channel) for m in await modes.for_channels(CTX)] == [
        ("agent", KNOWLEDGE), ("quick", KNOWLEDGE), ("numbers", ChannelResource("data_source", "数据源"))]
    assert [m.id for m in await modes.for_channels(ModeContext("ws", "outsider"))] == ["agent", "quick"]
    assert modes.channel_resource("numbers") == ChannelResource("data_source", "数据源")
    assert modes.channel_resource("modeling") is None and modes.channel_resource("missing") is None
    assert modes.channel_types() == frozenset({"knowledge_base", "data_source"})
    with pytest.raises(ValueError):
        modes.add(AnswerMode("bad", "坏的", "", channel=ChannelResource("Data Source", "数据源")))
