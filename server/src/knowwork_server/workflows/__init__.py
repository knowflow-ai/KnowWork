"""固定工作流：技能包里的 workflow.yaml 由业务服务按步骤驱动（技术方案 6.5 节退路，实验 E2 的结论）。

主线整合（不改 app.py 之外的模块，见最终报告）：
- 建表：WORKFLOW_TABLES（挂在 db.base.Base 上），行级安全：WORKFLOW_RLS_TABLES；
  另在 db/registry.py 登记模块 "knowwork_server.workflows.models"；
- 装配：`workflows = build_workflows(sessionmaker, tool_service, kernel, skills.delivery.catalog)`；
- 公开应用：`include_router(build_router(workflows.service, access_service, current_principal))`；
- 启动：`await workflows.engine.reconcile(STALE_AFTER)`；关闭：`await workflows.engine.aclose()`。
"""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from ..kernel.contract import AgentKernel
from ..tools.service import ToolService
from .agent import KernelAgentRunner
from .api import build_router
from .engine import WorkflowEngine
from .events import RunNotifier
from .models import WORKFLOW_RLS_TABLES, WORKFLOW_TABLES
from .service import Actor, SkillAvailability, WorkflowService
from .steps import ToolStepRunner
from .store import WorkflowStore

# 心跳 15 秒一次；超过 STALE_AFTER 没有心跳的 running 运行视为驱动者已死
HEARTBEAT_SECONDS = 15.0
STALE_AFTER = timedelta(seconds=60)


@dataclass(frozen=True)
class Workflows:
    service: WorkflowService
    engine: WorkflowEngine
    store: WorkflowStore
    notifier: RunNotifier


def build_workflows(sessionmaker: async_sessionmaker, tools: ToolService, kernel: AgentKernel,
                    catalog: SkillAvailability, *, heartbeat_seconds: float = HEARTBEAT_SECONDS) -> Workflows:
    store = WorkflowStore(sessionmaker)
    notifier = RunNotifier()
    engine = WorkflowEngine(store, ToolStepRunner(tools), KernelAgentRunner(kernel, tools), notify=notifier.notify,
                            heartbeat_seconds=heartbeat_seconds)
    service = WorkflowService(sessionmaker, store, engine, catalog, notifier)
    return Workflows(service=service, engine=engine, store=store, notifier=notifier)


__all__ = ["STALE_AFTER", "WORKFLOW_RLS_TABLES", "WORKFLOW_TABLES", "Actor", "Workflows", "build_router",
           "build_workflows"]
