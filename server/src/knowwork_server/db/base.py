from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

WORKSPACE_SETTING = "app.workspace_id"

# 这次请求（或这条后台活）在哪个工作空间：按空间的会话工厂在每个事务开始时读它。
# 设它的入口：请求按路径里的空间（app 的全局依赖）、宿主回调按令牌里的空间、事件泵按会话所在的空间、
# 后台任务按任务行的空间。没设时什么都不声明，行级安全下读不到、写不进（失败即拒绝），不会落到别的空间。
current_workspace: ContextVar[str | None] = ContextVar("current_workspace", default=None)


def enter_workspace(workspace_id: str) -> None:
    """把当前空间设成它，直到这个任务（及之后从它派生的任务）结束。请求入口与令牌核验处用。"""
    if not workspace_id:
        raise ValueError("workspace_id is required")
    current_workspace.set(workspace_id)


@contextmanager
def in_workspace(workspace_id: str) -> Iterator[None]:
    """在这段代码里以某个空间读写，出来恢复原样。一个长期运行的任务要轮流处理几个空间时用（批量写入、后台任务）。"""
    if not workspace_id:
        raise ValueError("workspace_id is required")
    token = current_workspace.set(workspace_id)
    try:
        yield
    finally:
        current_workspace.reset(token)


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    """不声明工作空间的会话工厂：只用于全局表，或由调用方在事务里自行 set_workspace。

    普通账号下用它读行级安全表一行都看不到（策略不成立），这是有意的失败即拒绝。
    """
    return async_sessionmaker(engine, expire_on_commit=False)


def make_workspace_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    """每个事务开始时声明当前空间（current_workspace），供行级安全判定（等价于 SET LOCAL，事务结束即失效）。

    对话、工具回执、产物、附件、后台任务等模块用它，调用方不用自己 set_workspace；没有当前空间时不声明。

    注意：传入的引擎不能配置 AUTOCOMMIT 隔离级别。set_config(..., true) 只在当前事务内有效，
    自动提交下它所在的语句一结束设置就失效，之后的查询看不到工作空间，行级安全下静默读空。
    """

    class WorkspaceSession(Session):
        pass

    @event.listens_for(WorkspaceSession, "after_begin")
    def _declare_workspace(session, transaction, connection) -> None:
        workspace_id = current_workspace.get()
        if workspace_id:
            connection.execute(text("SELECT set_config(:name, :value, true)"),
                               {"name": WORKSPACE_SETTING, "value": workspace_id})

    return async_sessionmaker(engine, expire_on_commit=False, sync_session_class=WorkspaceSession)
