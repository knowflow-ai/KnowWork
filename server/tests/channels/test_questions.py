"""IM 里回答智能体的追问（例如问数问「查哪个数据源」「按下单时间还是付款时间」）：

等人回答时不停下这一轮，把问题和带序号的选项发出去；同一个对话位置的下一条消息就是回答（回序号选选项，
写别的就是自己的说法；一次问了几个的，一条消息一起答），交回去以后接着跟这一轮，直到答完或又问。
问题已经结束（到期、在网页里答了）就当新的提问发。批准还是照旧（IM 里点不了）。"""

import pytest

from knowwork_server.conversations.replay import Outgoing
from knowwork_server.kernel.contract import PendingQuestion, Question, QuestionOption

from .conftest import SEQ, FakeConnection, make_channel, run
from .test_people import member_channel, message, person

pytestmark = pytest.mark.anyio

WHICH = PendingQuestion(id="call-q1", state="open", questions=(Question(
    id="source", question="查哪个数据源？", options=(QuestionOption(label="销售库", description="订单与回款"),
                                                    QuestionOption(label="财务库")),),))


def asking(turn: int) -> list[Outgoing]:
    return run(turn, ("item.completed", {"kind": "message", "data": {"role": "assistant", "text": "先确认一下。"}}),
               ("item.started", {"kind": "input_request", "item_id": "call-q1"}),
               ("run.waiting", {"item_id": "call-q1", "data": {"reason": "input"}}))


def resumed(turn: int, text: str) -> list[Outgoing]:
    """回答交回去以后同一轮接着跑：没有新的 run.started。"""
    run_id = f"run-{turn}"
    return [Outgoing(sse_id=str(next(SEQ)), type=kind, payload={"run_id": run_id, **payload}) for kind, payload in (
        ("item.completed", {"kind": "input_request", "item_id": "call-q1", "data": {"answers": []}}),
        ("item.completed", {"kind": "message", "data": {"role": "assistant", "text": text}}),
        ("run.completed", {}))]


async def test_the_bot_asks_in_chat_and_takes_the_next_message_as_the_answer(env):
    channel = await make_channel(env)
    env.conversations.asks = {"call-q1": WHICH}
    env.conversations.scripts = [asking(1), resumed(1, "销售库上个月 120 万。")]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, "上个月销售额", group="chat-1"), connection)
    assert connection.streams[0].final == ("先确认一下。\n\n**查哪个数据源？**\n1. 销售库（订单与回款）\n2. 财务库\n"
                                           "回复序号，或者直接写你的说法。")
    assert env.conversations.cancelled == []  # 不停下，等他回

    await env.channels.service.handle(message(channel, "1", group="chat-1"), connection)
    [(_, question_id, reply)] = env.conversations.answered
    assert (question_id, reply.answers[0].id, reply.answers[0].selected) == ("call-q1", "source", ("销售库",))
    assert len(env.conversations.sent) == 1  # 回答不是新的提问
    assert connection.streams[1].final == "销售库上个月 120 万。"


async def test_free_text_and_several_questions(env):
    channel = await member_channel(env)
    await person(env, "u-zhang", "zhangsan")
    env.conversations.asks = {"call-q1": WHICH}
    env.conversations.scripts = [asking(1), resumed(1, "好的")]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, "上个月销售额"), connection)
    await env.channels.service.handle(message(channel, "两个库都要"), connection)
    reply = env.conversations.answered[0][2]
    assert (reply.answers[0].selected, reply.answers[0].custom) == ((), "两个库都要")

    two = PendingQuestion(id="call-q1", state="open", questions=(
        Question(id="time", question="按哪个时间算？", options=(QuestionOption(label="下单时间"),
                                                            QuestionOption(label="付款时间"))),
        Question(id="scope", question="含退款吗？")))
    env.conversations.asks = {"call-q1": two}
    env.conversations.scripts = [asking(2), resumed(2, "好的")]
    await env.channels.service.handle(message(channel, "本月销售额"), connection)
    assert connection.streams[2].final == ("先确认一下。\n\n有几个问题要你补充，在一条消息里一起回答就行：\n"
                                           "1. 按哪个时间算？（可选：下单时间、付款时间）\n2. 含退款吗？")
    await env.channels.service.handle(message(channel, "按付款时间，不含退款"), connection)
    reply = env.conversations.answered[1][2]
    assert [(a.id, a.custom) for a in reply.answers] == [("time", "按付款时间，不含退款"),
                                                         ("scope", "按付款时间，不含退款")]


async def test_a_finished_question_turns_the_message_into_a_new_turn(env):
    channel = await make_channel(env)
    env.conversations.asks = {"call-q1": WHICH}
    env.conversations.scripts = [asking(1)]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, "上个月销售额"), connection)
    env.conversations.pending = []  # 到期了，或在网页里答过了
    env.conversations.scripts = [run(2, ("item.completed", {"kind": "message",
                                                            "data": {"role": "assistant", "text": "新的回答"}}),
                                     ("run.completed", {}))]
    await env.channels.service.handle(message(channel, "那这个月呢"), connection)
    assert env.conversations.answered == [] and len(env.conversations.sent) == 2
    assert connection.streams[1].final == "新的回答"
