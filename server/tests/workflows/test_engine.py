"""引擎：正常、审批拒绝、步骤失败终止、Schema 不合格重试、崩溃对账与续跑不重复写入、缓存命中。"""

import asyncio
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from knowwork_server.access.rls import set_workspace
from knowwork_server.skills.models import SkillRun, SkillStepRun
from knowwork_server.workflows.errors import RunConflict

from .conftest import APPROVAL, LOSE, actor, publish, settle, until

pytestmark = pytest.mark.anyio


async def facts(env, run_id):
    async with env.sessionmaker() as session, session.begin():
        await set_workspace(session, "ws1")
        run = await session.get(SkillRun, run_id)
        steps = (await session.scalars(select(SkillStepRun).where(SkillStepRun.skill_run_id == run_id)
                                       .order_by(SkillStepRun.seq))).all()
        return run, [(s.step_key, s.step_type, s.status, s.approval_result) for s in steps]


async def start(env, inputs=None, skill_id=None, user="dave"):
    skill_id = skill_id or await publish(env)
    view = await env.workflows.service.start(actor(user), skill_id, inputs or {"contract_id": "c-1"})
    return skill_id, view.id


async def test_happy_path_runs_every_step_in_order(env):
    _skill, run_id = await start(env)
    waiting = await settle(env, run_id)
    assert waiting.status == "waiting_approval" and waiting.current_step == "confirm"
    assert [s.status for s in waiting.steps] == ["succeeded", "succeeded", "waiting_approval", "pending"]
    assert waiting.step("confirm").output == {"title": "确认复核意见", "summary": "ok"}
    assert env.tools.calls["kw_extract"] == [{"id": "c-1"}]
    assert env.tools.count("kw_write") == 0  # 审批前不执行写入

    spec = env.kernel.opened[0]  # agent 步骤：只开放 kw_search，其余工具与 DSH 内置技能、子 Agent 都收窄
    assert spec.session_id == f"wf.{run_id}.summarize.1" and spec.skills_token is None
    assert set(spec.policy.deny) == {"kw_extract", "kw_write", "kw_fail", "skill", "subagent"}
    claims = env.tool_service.verify(spec.tools_token)
    assert claims.tools == frozenset({"kw_search"}) and claims.user_id == "dave"
    prompt = env.kernel.sent[0][1]
    assert "复核合同 c-1" in prompt and "甲方-c-1" in prompt and '"required": ["summary"]' in prompt
    assert env.kernel.disposed == [spec.session_id]

    await env.workflows.service.decide(actor(), run_id, "confirm", True)
    done = await settle(env, run_id, statuses=("succeeded", "failed"))
    assert done.status == "succeeded"
    assert done.output == {"saved": {"saved": True, "text": "ok"}, "summary": "ok"}
    assert done.step("confirm").approval_result == "approved" and done.step("confirm").decided_by == "dave"
    assert env.tools.calls["kw_write"] == [{"text": "ok"}]
    run, steps = await facts(env, run_id)
    assert (run.status, run.selection_mode, run.ended_at is not None) == ("succeeded", "workflow", True)
    assert steps == [("extract", "tool", "succeeded", None), ("summarize", "model", "succeeded", None),
                     ("confirm", "approval", "succeeded", "approved"), ("save", "tool", "succeeded", None)]


async def test_rejected_approval_fails_the_whole_run(env):
    _skill, run_id = await start(env)
    await settle(env, run_id)
    view = await env.workflows.service.decide(actor(), run_id, "confirm", False)
    assert view.status == "failed" and view.failure_code == "approval_rejected"
    assert view.step("save").status == "pending"
    await asyncio.sleep(0.1)
    assert env.tools.count("kw_write") == 0
    run, steps = await facts(env, run_id)
    assert (run.status, run.failure_stage, run.failure_code) == ("failed", "step", "approval_rejected")
    assert steps[-1] == ("confirm", "approval", "failed", "rejected")
    with pytest.raises(RunConflict):  # 已经结束，不能再决定
        await env.workflows.service.decide(actor(), run_id, "confirm", True)


FAILING = """
version: 1
steps:
  - key: first
    type: tool
    tool: kw_fail
  - key: save
    type: tool
    tool: kw_write
    args: {text: x}
"""


async def test_step_failure_terminates_the_run(env):
    skill_id = await publish(env, FAILING)
    run_id = (await env.workflows.service.start(actor(), skill_id, {})).id
    done = await settle(env, run_id)
    assert done.status == "failed" and done.failure_code == "tool_failed"
    assert "上游系统拒绝了这次请求" in done.failure_message
    assert [s.status for s in done.steps] == ["failed", "pending"]
    assert env.tools.count("kw_write") == 0
    run, steps = await facts(env, run_id)
    assert (run.failure_stage, steps) == ("tool", [("first", "tool", "failed", None)])


async def test_optional_step_failure_is_skipped(env):
    skill_id = await publish(env, FAILING.replace("tool: kw_fail", "tool: kw_fail\n    optional: true"))
    run_id = (await env.workflows.service.start(actor(), skill_id, {})).id
    done = await settle(env, run_id)
    assert done.status == "succeeded"
    assert [s.status for s in done.steps] == ["skipped", "succeeded"]
    assert done.step("first").error_code == "tool_failed"


AGENT_ONLY = """
version: 1
steps:
  - key: summarize
    type: agent
    prompt: 总结
    output_schema: {type: object, properties: {summary: {type: string}}, required: [summary]}
"""


async def test_schema_mismatch_retries_then_succeeds(env):
    replies = iter(["不是 JSON", '{"other": 1}', '```json\n{"summary": "第三次"}\n```'])
    env.kernel.responder = lambda _sid, _text, _n: next(replies)
    skill_id = await publish(env, AGENT_ONLY)
    run_id = (await env.workflows.service.start(actor(), skill_id, {})).id
    done = await settle(env, run_id)
    assert done.status == "succeeded" and done.steps[0].output == {"summary": "第三次"}
    texts = [text for _sid, text in env.kernel.sent]
    assert len(texts) == 3 and "不是合法的 JSON" in texts[1] and "summary" in texts[2]
    assert len(env.kernel.opened) == 1  # 同一个子会话里重试


async def test_schema_mismatch_fails_after_two_retries(env):
    env.kernel.responder = lambda _sid, _text, _n: '{"other": 1}'
    skill_id = await publish(env, AGENT_ONLY)
    run_id = (await env.workflows.service.start(actor(), skill_id, {})).id
    done = await settle(env, run_id)
    assert done.status == "failed" and done.failure_code == "output_schema_mismatch"
    assert len(env.kernel.sent) == 3
    run, _steps = await facts(env, run_id)
    assert run.failure_stage == "generation"


async def test_agent_tool_approval_and_turn_failure_fail_the_step(env):
    env.kernel.responder = lambda _sid, _text, _n: APPROVAL
    skill_id = await publish(env, AGENT_ONLY)
    done = await settle(env, (await env.workflows.service.start(actor(), skill_id, {})).id)
    assert done.failure_code == "approval_not_supported"
    env.kernel.responder = lambda _sid, _text, _n: None
    original = env.kernel._push

    def failing_push(session_id, kind, data):  # 把本轮结束改成出错
        if data.get("type") == "turn/end":
            data = {**data, "data": {**data["data"], "reason": {"kind": "error", "error": {"code": "LLM"}}}}
        original(session_id, kind, data)

    env.kernel._push = failing_push
    done = await settle(env, (await env.workflows.service.start(actor(), skill_id, {})).id)
    assert done.failure_code == "agent_turn_failed"


async def test_lost_runtime_interrupts_and_resume_continues(env):
    env.kernel.responder = lambda _sid, _text, n: LOSE
    skill_id = await publish(env, AGENT_ONLY)
    run_id = (await env.workflows.service.start(actor(), skill_id, {})).id
    interrupted = await settle(env, run_id)
    assert interrupted.status == "interrupted" and interrupted.failure_code == "runtime_unavailable"
    assert interrupted.steps[0].status == "interrupted"
    env.kernel.responder = lambda _sid, _text, _n: '{"summary": "恢复了"}'
    await env.workflows.service.resume(actor(), run_id)
    done = await settle(env, run_id, statuses=("succeeded", "failed"))
    assert done.status == "succeeded" and done.steps[0].attempts == 2
    assert env.kernel.opened[-1].session_id == f"wf.{run_id}.summarize.2"
    with pytest.raises(RunConflict):
        await env.workflows.service.resume(actor(), run_id)


WRITE_ONLY = """
version: 1
inputs: {type: object, properties: {text: {type: string}}}
steps:
  - key: save
    type: tool
    tool: kw_write
    args: {text: "${inputs.text}"}
  - key: after
    type: tool
    tool: kw_extract
    args: {id: "${steps.save.output.text}"}
"""


class Crash(BaseException):
    """模拟进程被杀：不是 Exception，引擎的兜底接不住，运行停在 running。"""


async def test_crash_after_write_then_reconcile_and_resume_does_not_write_twice(env):
    skill_id = await publish(env, WRITE_ONLY)
    store = env.workflows.store
    original = store.step_finished

    async def crash_once(workspace_id, run_id, driver_id, end, **kwargs):
        if end.key == "save" and end.status == "succeeded" and not crash_once.fired:
            crash_once.fired = True
            raise Crash()
        return await original(workspace_id, run_id, driver_id, end, **kwargs)

    crash_once.fired = False
    store.step_finished = crash_once
    run_id = (await env.workflows.service.start(actor(), skill_id, {"text": "合同"})).id
    await until(lambda: crash_once.fired)
    await env.workflows.engine.wait(run_id)
    stuck = await store.load("ws1", run_id)
    assert stuck.status == "running" and stuck.step("save").status == "running"
    assert env.tools.count("kw_write") == 1

    restarted = env.rebuild()  # 新进程：先对账，再由用户续跑
    assert await restarted.engine.reconcile(timedelta(seconds=0)) == [("ws1", run_id)]
    interrupted = await restarted.store.load("ws1", run_id)
    assert interrupted.status == "interrupted" and interrupted.step("save").status == "interrupted"
    assert interrupted.inputs == {"text": "合同"}  # 入参在库里，续跑不用重给
    await restarted.service.resume(actor(), run_id)
    done = await settle(restarted, run_id)
    await restarted.engine.aclose()
    assert done.status == "succeeded"
    assert env.tools.count("kw_write") == 1  # 同一幂等键（运行 ID + 步骤键）从动作回执重放
    assert env.tools.calls["kw_extract"] == [{"id": "合同"}]


async def test_crash_during_write_resumes_as_interrupted_until_receipt_settles(env):
    skill_id = await publish(env, WRITE_ONLY)
    env.tools.gates["kw_write"] = asyncio.Event()
    run_id = (await env.workflows.service.start(actor(), skill_id, {"text": "t"})).id
    await until(lambda: env.tools.count("kw_write") == 1)
    task = env.workflows.engine._tasks[run_id]
    task.cancel()  # 不是正常关闭：运行仍记为 running，回执停在执行中
    await asyncio.gather(task, return_exceptions=True)
    restarted = env.rebuild()
    await restarted.engine.reconcile(timedelta(seconds=0))
    await restarted.service.resume(actor(), run_id)
    again = await settle(restarted, run_id)
    assert again.status == "interrupted" and again.failure_code == "action_in_progress"
    assert env.tools.count("kw_write") == 1
    await restarted.engine.aclose()


async def test_reconcile_leaves_fresh_and_waiting_runs_alone(env):
    _skill, run_id = await start(env)
    await settle(env, run_id)
    assert await env.workflows.engine.reconcile(timedelta(seconds=0)) == []  # 等待审批不算没结束的驱动
    skill_id = await publish(env, AGENT_ONLY, name="slow-agent")
    original = env.kernel.send

    async def never(session_id, turn):  # 让 agent 步骤一直没有结果
        await asyncio.Event().wait()

    env.kernel.send = never
    slow = (await env.workflows.service.start(actor(), skill_id, {})).id
    await until(lambda: len(env.kernel.opened) == 2)
    await asyncio.sleep(0.15)  # 跨过几次心跳
    assert await env.workflows.engine.reconcile(timedelta(seconds=30)) == []  # 心跳新鲜
    env.kernel.send = original
    await env.workflows.service.cancel(actor(), slow)
    cancelled = await env.workflows.store.load("ws1", slow)
    assert cancelled.status == "cancelled" and cancelled.steps[0].status == "cancelled"


async def test_shutdown_marks_running_runs_interrupted(env):
    skill_id = await publish(env, AGENT_ONLY)

    async def never(session_id, turn):
        await asyncio.Event().wait()

    env.kernel.send = never
    run_id = (await env.workflows.service.start(actor(), skill_id, {})).id
    await until(lambda: len(env.kernel.opened) == 1)
    await env.workflows.engine.aclose()
    view = await env.workflows.store.load("ws1", run_id)
    assert view.status == "interrupted" and view.failure_code == "shutdown"


CACHED = """
version: 1
inputs: {type: object, properties: {contract_id: {type: string}}}
steps:
  - key: extract
    type: tool
    tool: kw_extract
    args: {id: "${inputs.contract_id}"}
  - key: summarize
    type: agent
    prompt: 总结
    tools: [kw_search]
    input: "${steps.extract.output}"
    output_schema: {type: object, properties: {summary: {type: string}}, required: [summary]}
  - key: save
    type: tool
    tool: kw_write
    args: {text: "${steps.summarize.output.summary}"}
"""


async def test_cache_reuses_validated_read_results_but_never_writes(env):
    skill_id = await publish(env, CACHED)
    first = await settle(env, (await env.workflows.service.start(actor(), skill_id, {"contract_id": "a"})).id)
    second = await settle(env, (await env.workflows.service.start(actor(), skill_id, {"contract_id": "a"})).id)
    assert first.status == second.status == "succeeded"
    assert [s.cache_hit for s in first.steps] == [False, False, False]
    assert [s.cache_hit for s in second.steps] == [True, True, False]
    assert env.tools.count("kw_extract") == 1 and len(env.kernel.sent) == 1
    assert env.tools.count("kw_write") == 2  # 写入每次运行都真实执行

    third = await settle(env, (await env.workflows.service.start(actor(), skill_id, {"contract_id": "b"})).id)
    assert [s.cache_hit for s in third.steps] == [False, False, False]  # 输入变了，下游跟着重算
    assert env.tools.count("kw_extract") == 2 and len(env.kernel.sent) == 2

    other = await publish(env, CACHED, user="erin", name="contract-review")
    fourth = await settle(env, (await env.workflows.service.start(actor("erin"), other, {"contract_id": "a"})).id)
    assert [s.cache_hit for s in fourth.steps] == [False, False, False]  # 不跨用户复用


async def test_invalid_results_are_not_cached(env):
    env.kernel.responder = lambda _sid, _text, _n: '{"other": 1}'
    skill_id = await publish(env, AGENT_ONLY)
    await settle(env, (await env.workflows.service.start(actor(), skill_id, {})).id)
    env.kernel.responder = lambda _sid, _text, _n: '{"summary": "好了"}'
    done = await settle(env, (await env.workflows.service.start(actor(), skill_id, {})).id)
    assert done.status == "succeeded" and done.steps[0].cache_hit is False


async def test_snapshot_is_immutable_across_republish(env):
    skill_id = await publish(env, AGENT_ONLY)
    run_id = (await env.workflows.service.start(actor(), skill_id, {})).id
    before = await settle(env, run_id)
    assert json.dumps(before.definition.to_json()) == json.dumps((await env.workflows.store.load("ws1", run_id))
                                                                .definition.to_json())
    assert before.skill_version and len(before.definition_digest) == 64
