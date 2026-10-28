"""渠道的统一处理：不认人，私聊、群聊都用渠道账号回答，每个对话位置一个会话、记下提问人的名字；流式回发、
不带网页链接；没选知识库、渠道账号被移出空间时说明原因；新会话命令；重投的消息不重复答；只认发出这一轮之后的
事件；停在等人确认或跟太久时取消这一轮。"""

import pytest

from knowwork_server.channels.types import IncomingMessage
from knowwork_server.conversations.replay import Outgoing
from knowwork_server.db.base import in_workspace

from .conftest import FakeConnection, answer, make_channel, run

pytestmark = pytest.mark.anyio


def message(channel, text="报销标准是多少", *, user="zhangsan", name="张三", group="", msgid=None):
    return IncomingMessage(channel_id=channel.id, workspace_id=channel.workspace_id, platform="wecom",
                           external_id=user, chat_type="group" if group else "direct", chat_id=group,
                           message_id=msgid or f"m-{text}-{user}-{group}", text=text, display_name=name)


async def sessions(env, channel):
    with in_workspace(env.ws):
        return await env.channels.admin.sessions("boss", env.ws, channel.id)


async def test_private_chats_are_answered_by_the_channel_account(env):
    channel = await make_channel(env, answer_mode="quick")
    env.conversations.scripts = [
        # 上一轮剩下的结束事件排在这一轮开始前面：不能当成这一轮的回答
        [*answer(1, "旧回答")[2:], *answer(2, "报销标准见差旅制度[1]，住宿每晚 500 元。")],
        answer(3, "第二个回答"),
    ]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel), connection)
    [conversation, title, selection] = env.conversations.created[0]
    # 会话属于渠道账号（和渠道同 id），不点名知识库：能查的就是授给它的
    assert (conversation.user_id, title, selection.knowledge_bases) == (channel.id, "报销标准是多少", None)
    [(sent_id, turn, _)] = env.conversations.sent
    assert (sent_id, turn.text, turn.answer_mode) == (conversation.id, "报销标准是多少", "quick")
    [stream] = connection.streams
    assert stream.placeholder == "正在思考…"
    assert stream.final == "报销标准见差旅制度，住宿每晚 500 元。"  # 去掉引用标记，不带网页链接
    # 第二条接着同一个会话
    await env.channels.service.handle(message(channel, "那出租车呢"), connection)
    assert len(env.conversations.created) == 1
    assert [sent[0] for sent in env.conversations.sent] == [conversation.id, conversation.id]
    assert connection.streams[1].final == "第二个回答"
    [seen] = await sessions(env, channel)
    assert (seen.external_id, seen.display_name, seen.chat_type, seen.conversation_id, seen.title) == (
        "zhangsan", "张三", "direct", conversation.id, "报销标准是多少")


async def test_in_groups_everyone_gets_their_own_conversation(env):
    channel = await make_channel(env)
    env.conversations.scripts = [answer(1, "回答张三"), answer(1, "回答李四")]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, group="chat-1"), connection)
    await env.channels.service.handle(message(channel, user="lisi", name="李四", group="chat-1"), connection)
    owners = {created[0].user_id for created in env.conversations.created}
    assert owners == {channel.id} and len(env.conversations.created) == 2
    assert [s.final for s in connection.streams] == ["回答张三", "回答李四"]
    seen = await sessions(env, channel)
    assert sorted((s.chat_type, s.chat_id, s.external_id, s.display_name) for s in seen) == [
        ("group", "chat-1", "lisi", "李四"), ("group", "chat-1", "zhangsan", "张三")]


async def test_new_conversation_command_starts_over(env):
    channel = await make_channel(env)
    env.conversations.scripts = [answer(1, "一"), answer(1, "二")]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, "第一问"), connection)
    await env.channels.service.handle(message(channel, "新会话"), connection)
    await env.channels.service.handle(message(channel, "第二问"), connection)
    assert connection.replies == ["已开始新会话，接下来的提问不再接着之前的对话。"]
    assert len({created[0].id for created in env.conversations.created}) == 2


async def test_conversation_deleted_on_the_web_is_replaced(env):
    channel = await make_channel(env)
    env.conversations.scripts = [answer(1, "一"), answer(1, "二")]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, "第一问"), connection)
    env.conversations.gone.add(env.conversations.created[0][0].id)
    await env.channels.service.handle(message(channel, "第二问"), connection)
    assert len(env.conversations.created) == 2


async def test_redelivered_messages_are_answered_once(env):
    channel = await make_channel(env)
    env.conversations.scripts = [answer(1, "一")]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, msgid="same"), connection)
    await env.channels.service.handle(message(channel, msgid="same"), connection)
    assert len(env.conversations.sent) == 1


async def test_without_bases_or_membership_the_bot_says_why(env):
    channel = await make_channel(env)
    connection = FakeConnection()
    with in_workspace(env.ws):
        await env.channels.admin._store.save(env.ws, channel.id, settings={"answer_mode": "agent", "bases": []})
    await env.channels.service.handle(message(channel, "第一问"), connection)
    assert connection.replies[-1] == "这个机器人还没选可查的知识库，请联系管理员。"

    with in_workspace(env.ws):
        await env.channels.admin._store.save(env.ws, channel.id, settings={"answer_mode": "agent",
                                                                           "bases": ["builtin.kb1"]})
    await env.access.remove_member("boss", env.ws, channel.id)  # 有人在成员页把渠道账号移出去了
    await env.channels.service.handle(message(channel, "第二问"), connection)
    assert connection.replies[-1] == "这个机器人的配置不完整，请联系管理员在渠道里重新保存一次。"
    assert env.conversations.created == []


async def test_failed_runs_are_reported(env):
    channel = await make_channel(env)
    env.conversations.scripts = [run(1, ("run.failed", {"data": {"message": "本轮执行失败"}}))]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel), connection)
    assert connection.streams[0].final == "（这一轮没有完成：本轮执行失败）"


async def test_turns_waiting_for_someone_are_cancelled(env):
    channel = await make_channel(env)
    env.conversations.scripts = [run(1, ("item.completed", {"kind": "message",
                                                            "data": {"role": "assistant", "text": "我先查一下"}}),
                                     ("run.waiting", {"data": {"reason": "approval"}}))]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel), connection)
    assert connection.streams[0].final == "我先查一下\n\n（需要人工确认才能继续，机器人这里做不了）"
    assert env.conversations.cancelled == [env.conversations.created[0][0].id]


async def test_turns_that_run_too_long_are_cancelled(env):
    channel = await make_channel(env)
    env.channels.service._answer_timeout = 0.05
    env.conversations.scripts = [[*run(1), Outgoing(sse_id=None, type="hang", payload={})]]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel), connection)
    assert connection.streams[0].final == "（回答时间太长，已停下，可以把问题拆小一点再问）"
    assert env.conversations.cancelled == [env.conversations.created[0][0].id]


async def test_agent_steps_become_progress_and_only_the_answer_stays(env):
    channel = await make_channel(env)
    env.channels.service._push_interval = 0
    say = lambda text: ("item.completed", {"kind": "message", "data": {"role": "assistant", "text": text}})
    tool = lambda name, arguments: ("item.started", {"kind": "tool", "data": {"name": name, "arguments": arguments}})
    env.conversations.scripts = [run(
        1,
        ("live", {"step": 1, "offset": 0, "text": "我先在知识库"}),
        say("我先在知识库里检索一下。"),
        tool("knowledge_search", '{"query": "微短剧 代表作品"}'),
        tool("knowledge_read", '{"doc_ref": "kb1/doc_1"}'),  # 这一步没说话：记工具在做什么，文档编号不给人看
        ("live", {"step": 3, "offset": 0, "text": "报告里"}),
        say("报告里的代表作品有《东栏雪》[1]。"),
        ("run.completed", {}),
    )]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel), connection)
    [stream] = connection.streams
    # 一步说的话先当正文流出来；调了工具就收进上方的进度块，只追加不替换
    assert stream.updates[0] == "我先在知识库"
    assert "> 我先在知识库里检索一下。\n> 阅读文档" in stream.updates
    assert stream.updates[-1] == "> 我先在知识库里检索一下。\n> 阅读文档\n\n报告里的代表作品有《东栏雪》。"
    assert stream.final == "报告里的代表作品有《东栏雪》。"  # 答完整条换成只有回答


async def test_tool_steps_without_narration_say_what_they_do(env):
    channel = await make_channel(env)
    env.channels.service._push_interval = 0
    env.conversations.scripts = [run(
        1,
        ("item.started", {"kind": "tool", "data": {"name": "knowledge_search", "arguments": '{"query": "差旅标准"}'}}),
        ("item.started", {"kind": "tool", "data": {"name": "mcp__crm__lookup", "arguments": "{}"}}),
        ("item.started", {"kind": "tool", "data": {"name": "kw_internal", "arguments": "{}"}}),
        ("item.completed", {"kind": "message", "data": {"role": "assistant", "text": "住宿每晚 500 元。"}}),
        ("run.completed", {}),
    )]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel), connection)
    [stream] = connection.streams
    assert "> 检索知识库：差旅标准\n> crm · lookup\n> 调用工具" in stream.updates
    assert stream.final == "住宿每晚 500 元。"
