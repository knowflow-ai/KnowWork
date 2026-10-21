"""按 workflow.yaml 推进一次运行（技术方案 6.5 节退路；E2 报告第 6 节的八条）。

推进规则：
- 每次运行按创建时冻结的定义快照与入参执行；步骤按定义顺序，已成功（或跳过）的步骤不再执行；
- 一个运行同一时刻只有一个驱动者（本进程的一个任务）。驱动者的每次写都带「运行仍归我」的条件，
  条件不成立（被取消、被对账收走）就停下；执行中每隔 heartbeat 秒续一次心跳；
- 步骤失败默认终止整个运行，失败阶段与错误码记在运行上；显式 optional 的步骤失败记为跳过、结果为 null；
- 审批步骤：步骤与运行都进入 waiting_approval，驱动者退出；外部批准后重新领取继续，拒绝即以失败结束；
- 基础设施问题（宿主不可用、回执仍在执行中）不算步骤失败：运行标为 interrupted，之后可续跑；
- 结果缓存：键 = 技能 + 用户 + 步骤定义 + 解析后的步骤输入 + 引用到的上游步骤结果摘要；只缓存通过校验的结果；
  只读工具与只用只读工具的 agent 步骤才进缓存，写入类工具每次运行都真实执行（靠动作回执去重）。
"""

import asyncio
import contextlib
import dataclasses
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from typing import Any, Protocol

from sqlalchemy.exc import SQLAlchemyError

from ..skills.workflow_def import AgentStep, ApprovalStep, ToolStep
from ..skills.workflow_refs import ReferenceUnresolved, find_refs, resolve
from ..tools.registry import ToolSpec
from .errors import RunConflict, StepFailed, StepInterrupted
from .steps import StepContext, StepResult
from .store import NewRun, RunEnd, StepEnd, StepStart, WorkflowStore
from .views import DONE_STEP, RunView, StepView

log = logging.getLogger(__name__)

MAX_OUTPUT_BYTES = 256 * 1024
CACHE_TTL = timedelta(hours=24)


class ToolRunner(Protocol):
    async def specs(self, ctx: StepContext) -> dict[str, ToolSpec]: ...

    async def run(self, ctx: StepContext, tool: str, arguments: dict[str, Any],
                  output_schema: dict[str, Any] | None) -> StepResult: ...


class AgentRunner(Protocol):
    async def run(self, ctx: StepContext, step: AgentStep, prompt: str, step_input: Any) -> StepResult: ...


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _fields(step: ToolStep | AgentStep | ApprovalStep) -> dict[str, Any]:
    if isinstance(step, ToolStep):
        return {"args": step.args}
    if isinstance(step, AgentStep):
        return {"prompt": step.prompt, "input": step.input}
    return {"title": step.title, "summary": step.summary}


class WorkflowEngine:
    def __init__(self, store: WorkflowStore, tools: ToolRunner, agents: AgentRunner, *,
                 notify: Callable[[str], None] = lambda _run_id: None, heartbeat_seconds: float = 15.0,
                 cache_ttl: timedelta = CACHE_TTL, driver_id: str | None = None) -> None:
        self._store = store
        self._tools = tools
        self._agents = agents
        self._notify = notify
        self._beat = heartbeat_seconds
        self._cache_ttl = cache_ttl
        self.driver_id = driver_id or uuid.uuid4().hex
        self._tasks: dict[str, asyncio.Task] = {}
        self._closing = False

    # ---- 对外操作（权限由 service 判定） ----

    async def start(self, run: NewRun) -> str:
        run_id = await self._store.create(run, self.driver_id)
        self._launch(run.workspace_id, run_id)
        return run_id

    async def resume(self, workspace_id: str, run_id: str) -> None:
        if not await self._store.claim(workspace_id, run_id, "interrupted", self.driver_id):
            raise RunConflict("not_resumable", "只有中断的运行可以续跑")
        self._launch(workspace_id, run_id)

    async def decide(self, workspace_id: str, run_id: str, step_key: str, user_id: str, allow: bool) -> RunView:
        run = await self._store.load(workspace_id, run_id)
        step = run.step(step_key) if any(s.key == step_key for s in run.steps) else None
        if step is None or step.type != "approval":
            raise RunConflict("not_waiting_approval", "这一步当前不在等待审批")
        output = {**(step.output or {}), "approved": allow}
        end = StepEnd(key=step.key, seq=step.seq, type="approval", status="succeeded" if allow else "failed",
                      output=output if allow else None, output_digest=digest(output) if allow else None,
                      approval_result="approved" if allow else "rejected", decided_by=user_id,
                      error_code=None if allow else "approval_rejected",
                      error_message=None if allow else "审批被拒绝")
        view = await self._store.decide(workspace_id, run_id, end, self.driver_id)
        self._notify(run_id)
        if allow:
            self._launch(workspace_id, run_id)
        return view

    async def cancel(self, workspace_id: str, run_id: str) -> None:
        if not await self._store.cancel(workspace_id, run_id):
            raise RunConflict("run_finished", "这次运行已经结束")
        task = self._tasks.get(run_id)
        if task is not None:
            task.cancel()
        self._notify(run_id)

    async def reconcile(self, stale_after: timedelta) -> list[tuple[str, str]]:
        """启动时调用：把没有活着的驱动者、却仍记为 running 的运行标为中断。"""
        swept = await self._store.reconcile(stale_after)
        for _workspace_id, run_id in swept:
            self._notify(run_id)
        return swept

    async def wait(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(task)

    async def aclose(self) -> None:
        """进程关闭：停下本进程驱动的运行，标为中断，可由任意实例续跑。"""
        self._closing = True
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---- 驱动 ----

    def _launch(self, workspace_id: str, run_id: str) -> None:
        task = asyncio.create_task(self._drive(workspace_id, run_id), name=f"workflow-{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None) if self._tasks.get(run_id) is _t else None)

    async def _drive(self, workspace_id: str, run_id: str) -> None:
        try:
            async with self._heartbeat(workspace_id, run_id):
                await self._advance(workspace_id, run_id)
        except asyncio.CancelledError:
            if self._closing:
                await self._interrupt(workspace_id, run_id, "shutdown", "业务服务关闭，运行被中断，可以续跑")
            raise
        except Exception:
            log.exception("workflow run %s crashed", run_id)
            await self._interrupt(workspace_id, run_id, "internal_error", "运行出现内部错误，已中断，可以续跑")
        finally:
            self._notify(run_id)

    async def _interrupt(self, workspace_id: str, run_id: str, code: str, message: str) -> None:
        try:
            await asyncio.shield(self._store.interrupt(workspace_id, run_id, self.driver_id, code, message))
        except Exception:
            log.exception("failed to mark workflow run %s interrupted", run_id)

    @contextlib.asynccontextmanager
    async def _heartbeat(self, workspace_id: str, run_id: str) -> AsyncIterator[None]:
        driver = asyncio.current_task()

        async def beat() -> None:
            while True:
                await asyncio.sleep(self._beat)
                try:
                    alive = await self._store.heartbeat(workspace_id, run_id, self.driver_id)
                except (SQLAlchemyError, OSError):
                    log.warning("workflow heartbeat failed for %s", run_id)
                    continue
                if not alive:  # 运行已被取消或被别的实例收走：停下正在执行的步骤
                    driver.cancel()
                    return

        ticker = asyncio.create_task(beat())
        try:
            yield
        finally:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker

    async def _advance(self, workspace_id: str, run_id: str) -> None:
        while True:
            run = await self._store.load(workspace_id, run_id)
            if run.status != "running" or run.definition is None:
                return
            pending = next((i for i, step in enumerate(run.steps) if step.status not in DONE_STEP), None)
            keep_going = await (self._complete(run) if pending is None else self._step(run, pending))
            self._notify(run_id)
            if not keep_going or pending is None:
                return

    async def _complete(self, run: RunView) -> bool:
        try:
            output = resolve(run.definition.output, run.inputs, run.outputs())
        except ReferenceUnresolved as error:
            end = RunEnd(status="failed", failure_stage="delivery", failure_code="output_unresolved",
                         failure_message=str(error)[:500])
        else:
            end = RunEnd(status="succeeded", output=output)
        return await self._store.finish(run.workspace_id, run.id, self.driver_id, end)

    async def _step(self, run: RunView, index: int) -> bool:
        step = run.definition.steps[index]
        view = run.steps[index]
        try:
            resolved = resolve(_fields(step), run.inputs, run.outputs())
        except ReferenceUnresolved as error:
            return await self._failed(run, step, view, StepFailed("reference_unresolved", str(error)))
        if isinstance(step, ApprovalStep):
            return await self._open_gate(run, view, resolved)
        ctx = StepContext(workspace_id=run.workspace_id, user_id=run.user_id, run_id=run.id, step_key=step.key)
        cache_key = await self._cache_key(run, step, ctx, resolved)
        if cache_key is not None and (hit := await self._store.cached(run.workspace_id, cache_key, self._cache_ttl)):
            return await self._store.step_finished(run.workspace_id, run.id, self.driver_id, StepEnd(
                key=step.key, seq=view.seq, type=step.type, status="succeeded", output=hit.output,
                output_digest=hit.output_digest, cache_hit=True, cache_key=cache_key,
                tool_name=getattr(step, "tool", None)))
        return await self._execute(run, step, view, ctx, resolved, cache_key)

    async def _execute(self, run: RunView, step, view: StepView, ctx: StepContext, resolved: dict[str, Any],
                       cache_key: str | None) -> bool:
        attempt = await self._store.step_started(run.workspace_id, run.id, self.driver_id, StepStart(
            key=step.key, seq=view.seq, type=step.type, cache_key=cache_key, tool_name=getattr(step, "tool", None)))
        if attempt is None:
            return False
        try:
            result = await self._invoke(step, dataclasses.replace(ctx, attempt=attempt), resolved)
        except StepInterrupted as error:
            await self._store.interrupt(run.workspace_id, run.id, self.driver_id, error.code, error.message)
            return False
        except StepFailed as error:
            return await self._failed(run, step, view, error)
        output_digest = digest(result.output)
        if len(canonical(result.output).encode()) > MAX_OUTPUT_BYTES:
            return await self._failed(run, step, view, StepFailed("output_too_large", "步骤结果超过 256KB 上限"))
        saved = await self._store.step_finished(run.workspace_id, run.id, self.driver_id, StepEnd(
            key=step.key, seq=view.seq, type=step.type, status="succeeded", output=result.output,
            output_digest=output_digest, cache_key=cache_key, tool_name=result.tool_name, action_id=result.action_id))
        if saved and cache_key is not None:
            await self._store.remember(run.workspace_id, cache_key, result.output, output_digest)
        return saved

    async def _invoke(self, step, ctx: StepContext, resolved: dict[str, Any]) -> StepResult:
        if isinstance(step, ToolStep):
            return await self._tools.run(ctx, step.tool, resolved["args"], step.output_schema)
        return await self._agents.run(ctx, step, str(resolved["prompt"]), resolved["input"])

    async def _cache_key(self, run: RunView, step, ctx: StepContext, resolved: dict[str, Any]) -> str | None:
        if not step.cache:
            return None
        names = (step.tool,) if isinstance(step, ToolStep) else step.tools
        specs = await self._tools.specs(ctx)
        if any(name not in specs or specs[name].risk != "read" for name in names):
            return None
        upstream = {ref.step: run.step(ref.step).output_digest
                    for ref in find_refs(_fields(step)) if ref.root == "steps"}
        return digest({"v": 1, "skill": run.skill_id, "user": run.user_id, "step": step.model_dump(mode="json"),
                       "input": resolved, "upstream": upstream})

    async def _open_gate(self, run: RunView, view: StepView, resolved: dict[str, Any]) -> bool:
        gate = {"title": str(resolved["title"])[:200], "summary": str(resolved["summary"])[:4000]}
        await self._store.step_finished(run.workspace_id, run.id, self.driver_id, StepEnd(
            key=view.key, seq=view.seq, type="approval", status="waiting_approval", output=gate),
            run_status="waiting_approval")
        return False

    async def _failed(self, run: RunView, step, view: StepView, error: StepFailed) -> bool:
        end = StepEnd(key=step.key, seq=view.seq, type=step.type, status="skipped" if step.optional else "failed",
                      tool_name=getattr(step, "tool", None), error_code=error.code, error_message=error.message[:500])
        if not await self._store.step_finished(run.workspace_id, run.id, self.driver_id, end):
            return False
        if step.optional:
            return True
        await self._store.finish(run.workspace_id, run.id, self.driver_id, RunEnd(
            status="failed", failure_stage=error.stage, failure_code=error.code,
            failure_message=f"步骤 {step.key} 失败：{error.message}"[:500]))
        return False
