"""运行与步骤的不可变视图。引擎按它推进，接口与事件流按它输出。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..skills.workflow_def import WorkflowDefinition, load_definition
from .models import WorkflowRun, WorkflowStep

TERMINAL_RUN = frozenset({"succeeded", "failed", "cancelled"})
DONE_STEP = frozenset({"succeeded", "skipped"})


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass(frozen=True)
class StepView:
    key: str
    seq: int
    type: str
    status: str
    attempts: int = 0
    cache_hit: bool = False
    output: Any = None
    output_digest: str | None = None
    tool_name: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    approval_result: str | None = None
    decided_by: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return {"key": self.key, "seq": self.seq, "type": self.type, "status": self.status,
                "attempts": self.attempts, "cacheHit": self.cache_hit, "output": self.output,
                "toolName": self.tool_name, "errorCode": self.error_code, "errorMessage": self.error_message,
                "approvalResult": self.approval_result, "decidedBy": self.decided_by,
                "startedAt": _iso(self.started_at), "endedAt": _iso(self.ended_at)}


@dataclass(frozen=True)
class RunView:
    id: str
    workspace_id: str
    user_id: str
    skill_id: str
    skill_name: str
    skill_version: str
    definition: WorkflowDefinition
    definition_digest: str
    inputs: dict[str, Any]
    status: str
    current_step: str | None
    output: Any
    failure_code: str | None
    failure_message: str | None
    version: int
    created_at: datetime | None
    ended_at: datetime | None
    steps: tuple[StepView, ...]  # 按定义顺序；还没开始的步骤状态为 pending

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_RUN

    def step(self, key: str) -> StepView:
        return next(step for step in self.steps if step.key == key)

    def outputs(self) -> dict[str, Any]:
        """已完成步骤的结果，供引用解析；跳过的可选步骤结果为 null。"""
        return {step.key: step.output for step in self.steps if step.status in DONE_STEP}

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "skillId": self.skill_id, "skillName": self.skill_name,
                "skillVersion": self.skill_version, "definitionDigest": self.definition_digest,
                "inputs": self.inputs, "status": self.status, "currentStep": self.current_step,
                "output": self.output, "failureCode": self.failure_code, "failureMessage": self.failure_message,
                "version": self.version, "createdAt": _iso(self.created_at), "endedAt": _iso(self.ended_at),
                "steps": [step.to_json() for step in self.steps]}


def _step_view(row: WorkflowStep) -> StepView:
    return StepView(key=row.step_key, seq=row.seq, type=row.step_type, status=row.status, attempts=row.attempts,
                    cache_hit=row.cache_hit, output=row.output, output_digest=row.output_digest,
                    tool_name=row.tool_name, error_code=row.error_code, error_message=row.error_message,
                    approval_result=row.approval_result, decided_by=row.decided_by, started_at=row.started_at,
                    ended_at=row.ended_at)


def run_view(row: WorkflowRun, steps: list[WorkflowStep]) -> RunView:
    definition = load_definition(row.definition)
    by_key = {step.step_key: step for step in steps}
    views = tuple(_step_view(by_key[step.key]) if step.key in by_key
                  else StepView(key=step.key, seq=index, type=step.type, status="pending")
                  for index, step in enumerate(definition.steps))
    return RunView(id=row.id, workspace_id=row.workspace_id, user_id=row.user_id, skill_id=row.skill_id,
                   skill_name=row.skill_name, skill_version=row.skill_version, definition=definition,
                   definition_digest=row.definition_digest, inputs=dict(row.inputs), status=row.status,
                   current_step=row.current_step, output=row.output, failure_code=row.failure_code,
                   failure_message=row.failure_message, version=row.version, created_at=row.created_at,
                   ended_at=row.ended_at, steps=views)
