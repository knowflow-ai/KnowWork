"""每轮汇总：回合开始插一行（进行中），结束时补齐提问、状态、起止时间、用过的工具和各模块的事实；
结束原因归成四种状态；模块算事实出错不挡汇总；进程重启后才收到结束帧的也能补上。"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from knowwork_server.conversations.models import Conversation, RunSummary
from knowwork_server.conversations.summaries import RunSummaries, TurnEnded

pytestmark = pytest.mark.anyio
WS = "ws_sum"
START, END = 1793025165294, 1793025175294  # 毫秒


def frame(kind: str, data: dict, time: int = START) -> tuple[str, dict]:
    return "session/event", {"type": kind, "time": time, "data": data}


def user(text: str, source: str = "user") -> dict:
    return {"role": "user", "source": {"kind": source}, "content": [{"type": "text", "text": text}]}


@pytest.fixture
async def env(sessionmaker):
    conversation = Conversation(id="c_" + uuid.uuid4().hex, workspace_id=WS, user_id="alice", title="合同")
    async with sessionmaker() as session:
        session.add(conversation)
        await session.commit()

    async def owner_of(conversation_id):
        return "alice" if conversation_id == conversation.id else None

    yield sessionmaker, RunSummaries(sessionmaker, owner_of), conversation.id


def feed(summaries: RunSummaries, cid: str, *frames) -> None:
    for kind, data in frames:
        summaries(cid, WS, kind, data)


async def summary(sessionmaker, cid: str, turn: int) -> RunSummary | None:
    async with sessionmaker() as session:
        return await session.scalar(select(RunSummary).where(RunSummary.conversation_id == cid,
                                                             RunSummary.turn == turn))


async def test_a_turn_is_running_at_start_and_complete_at_the_end(env):
    sessionmaker, summaries, cid = env
    seen: list[TurnEnded] = []

    async def knowledge(ended):
        seen.append(ended)
        return {"searches": 2, "cited": 1}

    summaries.contribute("knowledge", knowledge)
    feed(summaries, cid, frame("turn/start", {"turn": 2}), frame("user/message", user("合同有哪些条款")),
         frame("user/message", user("<runtime>当前时间</runtime>", "runtime-context")))
    assert summaries.current_turn(cid) == 2
    await summaries.drain()
    running = await summary(sessionmaker, cid, 2)
    assert (running.status, running.question, running.started_at, running.ended_at) == (
        "running", "合同有哪些条款", datetime.fromtimestamp(START / 1000, UTC), None), "进行中也看得到在问什么"

    feed(summaries, cid, frame("user/message", user("补充：只看付款")),  # 插话不算提问
         frame("tool/call", {"turn": 2, "callId": "t1", "name": "knowledge_search"}),
         frame("tool/call", {"turn": 2, "callId": "t2", "name": "knowledge_read"}),
         frame("tool/call", {"turn": 2, "callId": "t3", "name": "knowledge_search"}),
         frame("assistant/message", {"message": {"content": [{"type": "text", "text": "付款分两期 [1]"}]}}),
         frame("turn/end", {"turn": 2, "reason": {"kind": "completed"}}, time=END))
    assert summaries.current_turn(cid) is None
    await summaries.drain()
    done = await summary(sessionmaker, cid, 2)
    assert (done.status, done.end_reason, done.question, done.tools, done.facts, done.user_id) == (
        "completed", "completed", "合同有哪些条款", ["knowledge_search", "knowledge_read"],
        {"knowledge": {"searches": 2, "cited": 1}}, "alice")
    assert (done.started_at, done.ended_at) == (datetime.fromtimestamp(START / 1000, UTC),
                                                datetime.fromtimestamp(END / 1000, UTC))
    assert [(e.turn, e.question, e.answer, e.status) for e in seen] == [(2, "合同有哪些条款", "付款分两期 [1]",
                                                                        "completed")]


@pytest.mark.parametrize(("reason", "status"), [("aborted", "cancelled"), ("error", "failed"), ("blocked", "failed"),
                                                ("max-tokens", "completed"), (None, "failed")])
async def test_end_reasons_become_four_statuses(env, reason, status):
    sessionmaker, summaries, cid = env
    feed(summaries, cid, frame("turn/start", {"turn": 1}),
         frame("turn/end", {"turn": 1, "reason": {"kind": reason} if reason else {}}))
    await summaries.drain()
    assert (await summary(sessionmaker, cid, 1)).status == status


async def test_a_failing_module_does_not_block_the_summary(env):
    sessionmaker, summaries, cid = env

    async def broken(ended):
        raise RuntimeError("知识模块挂了")

    async def nothing(ended):
        return None

    summaries.contribute("knowledge", broken)
    summaries.contribute("skills", nothing)
    feed(summaries, cid, frame("turn/start", {"turn": 1}), frame("turn/end", {"turn": 1, "reason": {"kind": "completed"}}))
    await summaries.drain()
    done = await summary(sessionmaker, cid, 1)
    assert (done.status, done.facts) == ("completed", {})


async def test_an_end_seen_without_its_start_still_lands(env):
    """进程重启后才收到结束帧：进程里没记下这一轮，照样按结束帧补上一行；迟到的开始帧不会把它改回进行中。"""
    sessionmaker, summaries, cid = env
    feed(summaries, cid, frame("turn/end", {"turn": 4, "reason": {"kind": "aborted"}}, time=END))
    await summaries.drain()
    feed(summaries, cid, frame("turn/start", {"turn": 4}))
    await summaries.drain()
    done = await summary(sessionmaker, cid, 4)
    assert (done.status, done.question, done.tools, done.started_at) == (
        "cancelled", "", [], datetime.fromtimestamp(END / 1000, UTC))


async def test_frames_of_unknown_conversations_are_skipped(env):
    sessionmaker, summaries, _ = env
    feed(summaries, "c_gone", frame("turn/start", {"turn": 1}), frame("turn/end", {"turn": 1, "reason": {}}))
    await summaries.drain()
    assert await summary(sessionmaker, "c_gone", 1) is None
