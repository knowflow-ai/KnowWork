"""工具令牌：建会话与恢复时下发，续期循环替还开着的会话换新令牌，宿主已没有的会话停止续期。

另覆盖审查加固：续期与恢复交错不丢追踪（代际号）、有界并发与单次超时、续期复核成员身份。
"""

import asyncio
import itertools
import time

import pytest

from knowwork_server.conversations.listeners import Broadcaster
from knowwork_server.conversations.models import Conversation
from knowwork_server.conversations.pump import EventPump, PumpTimings
from knowwork_server.conversations.replay import EventFeed
from knowwork_server.conversations.service import ConversationService
from knowwork_server.conversations.tool_credentials import (
    ToolAccessRevoked,
    ToolCredentials,
    ToolCredentialUnavailable,
    require_membership,
)
from knowwork_server.conversations.writer import BatchWriter
from knowwork_server.kernel.contract import KernelError, SessionNotFound, TurnInput

from .fakes import FakeKernel

pytestmark = pytest.mark.anyio


class Issuer:
    def __init__(self):
        self.counter = itertools.count(1)
        self.calls = []

    async def __call__(self, conversation):
        self.calls.append((conversation.workspace_id, conversation.user_id, conversation.id))
        return f"tool-token-{next(self.counter):04d}-" + "x" * 16


def build(kernel, sessionmaker, issuer):
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster, timings=PumpTimings(idle_timeout=0.2))
    feed = EventFeed(sessionmaker, writer, broadcaster, pump.produced_upto)
    credentials = ToolCredentials(kernel, issuer, interval=3600)
    service = ConversationService(kernel, sessionmaker, pump, feed, tool_credentials=credentials,
                                  resume_retry_delays=(0.01,))
    return service, credentials, pump


async def test_open_and_resume_carry_a_fresh_tools_token(sessionmaker):
    kernel, issuer = FakeKernel(), Issuer()
    service, credentials, pump = build(kernel, sessionmaker, issuer)
    conversation = await service.create(workspace_id="default", user_id="u1")
    assert kernel.opened[0].tools_token.startswith("tool-token-0001")
    assert issuer.calls == [("default", "u1", conversation.id)]

    kernel.send_effects = [SessionNotFound("session_not_found", "gone", 404)]
    await service.send(conversation, TurnInput(text="hi"))
    assert kernel.opened[1].resume is True
    assert kernel.opened[1].tools_token.startswith("tool-token-0002")
    assert credentials.tracked() == {conversation.id}
    await pump.close()


async def test_renew_rotates_open_sessions_and_drops_gone_ones(sessionmaker):
    kernel, issuer = FakeKernel(), Issuer()
    service, credentials, pump = build(kernel, sessionmaker, issuer)
    first = await service.create(workspace_id="default", user_id="u1")
    second = await service.create(workspace_id="default", user_id="u2")

    kernel.tool_rotation_effects = [SessionNotFound("session_not_found", "gone", 404)]
    await credentials.renew_all()
    renewed = {sid for sid, _ in kernel.tool_rotations}
    assert len(renewed) == 1 and renewed <= {first.id, second.id}
    assert len(credentials.tracked()) == 1, "宿主上已不存在的会话应停止续期"

    kernel.tool_rotation_effects = [KernelError("internal", "boom", 500)]
    await credentials.renew_all()
    assert len(credentials.tracked()) == 1, "临时错误不应丢掉会话，下一轮再试"
    await pump.close()


async def test_without_tool_credentials_no_token_is_sent(sessionmaker):
    kernel = FakeKernel()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster)
    service = ConversationService(kernel, sessionmaker, pump, EventFeed(sessionmaker, writer, broadcaster,
                                                                        pump.produced_upto))
    await service.create(workspace_id="default", user_id="u1")
    assert kernel.opened[0].tools_token is None
    await pump.close()


async def test_issuer_failure_fails_the_open(sessionmaker):
    kernel = FakeKernel()

    async def broken(conversation):
        raise RuntimeError("signer down")

    service, credentials, pump = build(kernel, sessionmaker, broken)
    with pytest.raises(ToolCredentialUnavailable):
        await service.create(workspace_id="default", user_id="u1")
    assert kernel.opened == [] and credentials.tracked() == set()
    await pump.close()


def conv(cid: str, user_id: str = "u1") -> Conversation:
    return Conversation(id=cid, workspace_id="default", user_id=user_id, title="")


async def fixed_token(conversation):
    return "tool-token-" + "x" * 16


async def test_resume_during_renewal_keeps_the_session_tracked():
    """交错：续期签发后、轮换前，用户恢复并重新追踪同一会话；轮换报会话不存在也不能把它移出追踪。"""
    kernel = FakeKernel()
    entered, release = asyncio.Event(), asyncio.Event()

    async def gated(conversation):
        entered.set()
        await release.wait()
        return "tool-token-" + "x" * 16

    credentials = ToolCredentials(kernel, gated, interval=3600)
    credentials.track(conv("c_x"))
    kernel.tool_rotation_effects = [SessionNotFound("session_not_found", "gone", 404)]

    renewal = asyncio.create_task(credentials.renew_all())
    await entered.wait()
    credentials.track(conv("c_x"))  # 用户发消息走恢复路径，重新打开并追踪
    release.set()
    await renewal

    assert credentials.tracked() == {"c_x"}, "刚恢复的会话不能被旧一轮续期的 forget 移出"


def test_forget_with_stale_generation_is_a_no_op():
    credentials = ToolCredentials(FakeKernel(), fixed_token, interval=3600)
    old = credentials.track(conv("c_1"))
    new = credentials.track(conv("c_1"))
    assert new != old
    credentials.forget("c_1", generation=old)
    assert credentials.tracked() == {"c_1"}
    credentials.forget("c_1", generation=new)
    assert credentials.tracked() == set()
    credentials.track(conv("c_2"))
    credentials.forget("c_2")
    credentials.forget("missing")
    assert credentials.tracked() == set()


async def test_one_slow_issue_does_not_stall_the_round():
    kernel = FakeKernel()

    async def issue(conversation):
        if conversation.id == "c_slow":
            await asyncio.Event().wait()
        return "tool-token-" + conversation.id

    credentials = ToolCredentials(kernel, issue, interval=3600, call_timeout=0.05, concurrency=2)
    for cid in ("c_slow", "c_a", "c_b", "c_c"):
        credentials.track(conv(cid))
    started = time.monotonic()
    await credentials.renew_all()
    assert time.monotonic() - started < 1.0
    assert {sid for sid, _ in kernel.tool_rotations} == {"c_a", "c_b", "c_c"}
    assert credentials.tracked() == {"c_slow", "c_a", "c_b", "c_c"}, "超时只是这一轮没换上，下一轮再试"


async def test_slow_rotation_times_out_too():
    class SlowRotation(FakeKernel):
        async def rotate_tool_credential(self, session_id, token):
            await asyncio.Event().wait()

    credentials = ToolCredentials(SlowRotation(), fixed_token, interval=3600, call_timeout=0.05)
    credentials.track(conv("c_1"))
    await asyncio.wait_for(credentials.renew_all(), timeout=1.0)
    assert credentials.tracked() == {"c_1"}


async def test_renewal_concurrency_is_bounded():
    kernel = FakeKernel()
    state = {"now": 0, "peak": 0}

    async def issue(conversation):
        state["now"] += 1
        state["peak"] = max(state["peak"], state["now"])
        await asyncio.sleep(0.01)
        state["now"] -= 1
        return "tool-token-" + conversation.id

    credentials = ToolCredentials(kernel, issue, interval=3600, concurrency=3)
    for i in range(10):
        credentials.track(conv(f"c_{i}"))
    await credentials.renew_all()
    assert len(kernel.tool_rotations) == 10
    assert 1 < state["peak"] <= 3


async def test_renewal_drops_and_cancels_sessions_of_removed_members():
    kernel = FakeKernel()
    members = {"u1", "u2"}
    credentials = ToolCredentials(kernel, require_membership(fixed_token, lambda c: c.user_id in members),
                                  interval=3600)
    credentials.track(conv("c_1", "u1"))
    credentials.track(conv("c_2", "u2"))

    members.discard("u1")
    await credentials.renew_all()
    assert [sid for sid, _ in kernel.tool_rotations] == ["c_2"], "已被移出的成员拿不到新令牌"
    assert credentials.tracked() == {"c_2"}
    assert kernel.cancelled == ["c_1"], "被收回权限的会话正在跑的回合要取消"


async def test_cancel_failure_after_revocation_is_contained():
    class CancelFails(FakeKernel):
        async def cancel(self, session_id):
            raise KernelError("internal", "boom", 500)

    credentials = ToolCredentials(CancelFails(), require_membership(fixed_token, lambda c: False), interval=3600)
    credentials.track(conv("c_1"))
    await credentials.renew_all()
    assert credentials.tracked() == set()


async def test_token_for_maps_failures():
    async def broken(conversation):
        raise RuntimeError("signer secret detail")

    async def hang(conversation):
        await asyncio.Event().wait()

    with pytest.raises(ToolCredentialUnavailable) as caught:
        await ToolCredentials(FakeKernel(), broken).token_for(conv("c_1"))
    assert "secret" not in str(caught.value)
    with pytest.raises(ToolCredentialUnavailable):
        await ToolCredentials(FakeKernel(), hang, call_timeout=0.05).token_for(conv("c_1"))
    with pytest.raises(ToolAccessRevoked):
        await ToolCredentials(FakeKernel(), require_membership(fixed_token, lambda c: False)).token_for(conv("c_1"))
    assert await ToolCredentials(FakeKernel(), require_membership(fixed_token, lambda c: True)).token_for(
        conv("c_1")) == "tool-token-" + "x" * 16


async def test_membership_check_error_fails_closed():
    def explode(conversation):
        raise RuntimeError("db down")

    with pytest.raises(ToolCredentialUnavailable):
        await ToolCredentials(FakeKernel(), require_membership(fixed_token, explode)).token_for(conv("c_1"))


async def test_resume_refuses_removed_members(sessionmaker):
    kernel, members = FakeKernel(), {"u1"}
    issuer = require_membership(Issuer(), lambda c: c.user_id in members)
    service, _credentials, pump = build(kernel, sessionmaker, issuer)
    conversation = await service.create(workspace_id="default", user_id="u1")

    members.clear()
    kernel.send_effects = [SessionNotFound("session_not_found", "gone", 404)]
    with pytest.raises(ToolAccessRevoked):
        await service.send(conversation, TurnInput(text="hi"))
    assert len(kernel.opened) == 1, "权限被收回后不应重新打开宿主会话"
    await pump.close()


def test_invalid_settings_are_rejected():
    with pytest.raises(ValueError):
        ToolCredentials(FakeKernel(), fixed_token, concurrency=0)
    with pytest.raises(ValueError):
        ToolCredentials(FakeKernel(), fixed_token, call_timeout=0)


async def test_background_loop_renews_and_stops():
    kernel = FakeKernel()
    credentials = ToolCredentials(kernel, fixed_token, interval=0.01)
    credentials.track(conv("c_1"))
    credentials.start()
    credentials.start()  # 重复启动无副作用
    for _ in range(100):
        if kernel.tool_rotations:
            break
        await asyncio.sleep(0.01)
    await credentials.stop()
    await credentials.stop()
    assert kernel.tool_rotations and kernel.tool_rotations[0][0] == "c_1"


async def test_loop_survives_unexpected_round_failure(monkeypatch):
    credentials = ToolCredentials(FakeKernel(), fixed_token, interval=0.01)
    rounds = []

    async def flaky():
        rounds.append(1)
        if len(rounds) == 1:
            raise RuntimeError("unexpected")

    monkeypatch.setattr(credentials, "renew_due", flaky)
    credentials.start()
    for _ in range(100):
        if len(rounds) >= 2:
            break
        await asyncio.sleep(0.01)
    await credentials.stop()
    assert len(rounds) >= 2, "一轮意外失败不能让续期循环退出"


async def test_unknown_membership_is_not_revocation():
    """判定器不可用（None）：不签发，但既不停止追踪也不取消回合，建会话 / 恢复映射为签发失败。"""
    kernel = FakeKernel()
    credentials = ToolCredentials(kernel, require_membership(fixed_token, lambda c: None), interval=3600)
    with pytest.raises(ToolCredentialUnavailable):
        await credentials.token_for(conv("c_1"))
    credentials.track(conv("c_1"))
    await credentials.renew_all()
    assert credentials.tracked() == {"c_1"}
    assert kernel.tool_rotations == [] and kernel.cancelled == []


async def test_skill_token_rides_the_same_session_spec_and_renewal(sessionmaker):
    kernel, tools, skills = FakeKernel(), Issuer(), Issuer()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster, timings=PumpTimings(idle_timeout=0.2))
    feed = EventFeed(sessionmaker, writer, broadcaster, pump.produced_upto)
    skill_credentials = ToolCredentials(kernel, skills, interval=3600, rotate=kernel.rotate_skill_credential,
                                        label="skill")
    service = ConversationService(kernel, sessionmaker, pump, feed,
                                  tool_credentials=ToolCredentials(kernel, tools, interval=3600),
                                  skill_credentials=skill_credentials, resume_retry_delays=(0.01,))
    conversation = await service.create(workspace_id="default", user_id="u1")
    assert kernel.opened[0].tools_token.startswith("tool-token-0001")
    assert kernel.opened[0].skills_token.startswith("tool-token-0001")
    assert skills.calls == [("default", "u1", conversation.id)]
    await skill_credentials.renew_all()
    assert [sid for sid, _ in kernel.skill_rotations] == [conversation.id]
    assert kernel.tool_rotations == [], "技能令牌续期不该走工具令牌的续期接口"
    await pump.close()


class WallClock:
    def __init__(self):
        self.now = 1_000.0

    def __call__(self):
        return self.now


async def test_each_turn_refreshes_tokens_that_expired_while_the_computer_slept(sessionmaker):
    """续期循环按单调时钟可能还没轮到（macOS 睡眠时它不走）：发消息前按墙上时钟把到期的令牌续上，模型令牌也一样。"""
    kernel, issuer, clock = FakeKernel(), Issuer(), WallClock()
    writer, broadcaster = BatchWriter(sessionmaker), Broadcaster()
    pump = EventPump(kernel, sessionmaker, writer, broadcaster, timings=PumpTimings(idle_timeout=0.2))
    feed = EventFeed(sessionmaker, writer, broadcaster, pump.produced_upto)
    credentials = ToolCredentials(kernel, issuer, interval=300, clock=clock)
    model_refreshes = []

    async def refresh_model():
        model_refreshes.append(clock.now)

    service = ConversationService(kernel, sessionmaker, pump, feed, tool_credentials=credentials,
                                  refresh_model_credential=refresh_model, resume_retry_delays=(0.01,))
    conversation = await service.create(workspace_id="default", user_id="u1")
    await service.send(conversation, TurnInput(text="hi"))
    assert kernel.tool_rotations == [], "刚下发的令牌不用续"
    clock.now += 1_800  # 合盖半小时
    await service.send(conversation, TurnInput(text="还在吗"))
    assert [cid for cid, _ in kernel.tool_rotations] == [conversation.id]
    await service.send(conversation, TurnInput(text="再问一句"))
    assert len(kernel.tool_rotations) == 1, "续过之后重新计时"
    assert len(model_refreshes) == 3, "每轮都让模型令牌检查一次"
    await pump.close()


async def test_renew_due_skips_sessions_issued_recently():
    kernel, clock = FakeKernel(), WallClock()
    credentials = ToolCredentials(kernel, fixed_token, interval=300, clock=clock)
    credentials.track(conv("c_old"))
    clock.now += 200
    credentials.track(conv("c_new"))
    clock.now += 150
    await credentials.renew_due()
    assert [cid for cid, _ in kernel.tool_rotations] == ["c_old"]
    await credentials.renew_due()
    assert len(kernel.tool_rotations) == 1
