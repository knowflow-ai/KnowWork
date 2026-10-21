"""固定工作流的业务入口：权限判定、入参校验，然后交给引擎。

权限（失败一律拒绝）：
- 只有运行者本人能看、批准/拒绝、续跑、取消自己的运行；别人的运行一律当作不存在（404）；
- 启动、续跑、审批时技能必须此刻仍对运行者可用：按 skills 的会话目录判定（已发布、已启用、个人技能只给本人、
  空间技能按 RBAC 的 use），与对话里能用哪些技能是同一条规则；取消不要求技能可用（总能停下自己的运行）；
- 运行按发布版本的定义快照执行：之后技能重新发布，不影响已经开始的运行。
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from ..skills.models import SkillRelease
from ..skills.views import CatalogEntry
from ..skills.workflow_def import definition_digest, load_definition
from .engine import WorkflowEngine
from .errors import InputsInvalid, NotAWorkflow, RunNotFound, SkillUnavailable
from .events import RunNotifier, run_snapshots
from .store import NewRun, WorkflowStore
from .views import RunView

MAX_INPUT_BYTES = 256 * 1024
MAX_LIST = 50


class SkillAvailability(Protocol):
    """skills.catalog.SkillCatalog 满足这个协议。"""

    async def for_session(self, workspace_id: str, user_id: str) -> tuple[CatalogEntry, ...]: ...


@dataclass(frozen=True)
class Actor:
    user_id: str
    workspace_id: str


class WorkflowService:
    def __init__(self, sessionmaker: async_sessionmaker, store: WorkflowStore, engine: WorkflowEngine,
                 catalog: SkillAvailability, notifier: RunNotifier) -> None:
        self._sessionmaker = sessionmaker
        self._store = store
        self._engine = engine
        self._catalog = catalog
        self._notifier = notifier

    async def _entry(self, actor: Actor, skill_id: str) -> CatalogEntry:
        entries = await self._catalog.for_session(actor.workspace_id, actor.user_id)
        entry = next((e for e in entries if e.skill_id == skill_id), None)
        if entry is None:
            raise SkillUnavailable()
        return entry

    async def _release_manifest(self, workspace_id: str, release_id: str) -> dict[str, Any]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            manifest = await session.scalar(select(SkillRelease.manifest).where(SkillRelease.id == release_id))
        if manifest is None:
            raise SkillUnavailable()
        return manifest

    async def _own(self, actor: Actor, run_id: str) -> RunView:
        run = await self._store.load(actor.workspace_id, run_id)
        if run.user_id != actor.user_id:
            raise RunNotFound()
        return run

    async def start(self, actor: Actor, skill_id: str, inputs: dict[str, Any], *,
                    session_id: str | None = None) -> RunView:
        entry = await self._entry(actor, skill_id)
        manifest = await self._release_manifest(actor.workspace_id, entry.release_id)
        snapshot = manifest.get("workflow")
        if not isinstance(snapshot, dict):
            raise NotAWorkflow()
        definition = load_definition(snapshot)
        _check_inputs(definition.inputs, inputs)
        run_id = await self._engine.start(NewRun(
            workspace_id=actor.workspace_id, user_id=actor.user_id, skill_id=entry.skill_id, skill_name=entry.name,
            skill_version=entry.version, release_id=entry.release_id, definition=definition.to_json(),
            definition_digest=definition_digest(definition.to_json()), inputs=inputs, session_id=session_id))
        return await self._store.load(actor.workspace_id, run_id)

    async def get(self, actor: Actor, run_id: str) -> RunView:
        return await self._own(actor, run_id)

    async def list(self, actor: Actor, limit: int = 20) -> list[RunView]:
        return await self._store.list_for_user(actor.workspace_id, actor.user_id, max(1, min(limit, MAX_LIST)))

    async def decide(self, actor: Actor, run_id: str, step_key: str, allow: bool) -> RunView:
        run = await self._own(actor, run_id)
        if allow:  # 拒绝总是允许：它只会让运行结束
            await self._entry(actor, run.skill_id)
        return await self._engine.decide(actor.workspace_id, run_id, step_key, actor.user_id, allow)

    async def resume(self, actor: Actor, run_id: str) -> RunView:
        run = await self._own(actor, run_id)
        await self._entry(actor, run.skill_id)
        await self._engine.resume(actor.workspace_id, run_id)
        return await self._store.load(actor.workspace_id, run_id)

    async def cancel(self, actor: Actor, run_id: str) -> RunView:
        await self._own(actor, run_id)
        await self._engine.cancel(actor.workspace_id, run_id)
        return await self._store.load(actor.workspace_id, run_id)

    async def events(self, actor: Actor, run_id: str, after: int):
        await self._own(actor, run_id)
        return run_snapshots(self._store, self._notifier, actor.workspace_id, run_id, after)


def _check_inputs(schema: dict[str, Any], inputs: dict[str, Any]) -> None:
    if len(json.dumps(inputs, ensure_ascii=False).encode()) > MAX_INPUT_BYTES:
        raise InputsInvalid("inputs_too_large", "入参超过 256KB 上限")
    error = next(iter(Draft202012Validator(schema).iter_errors(inputs)), None)
    if error is not None:
        where = ".".join(str(part) for part in error.absolute_path) or "inputs"
        raise InputsInvalid("invalid_inputs", f"入参 {where} 不合法：{error.message[:200]}")
