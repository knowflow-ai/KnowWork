from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

WORKSPACE_SETTING = "app.workspace_id"


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    """不声明工作空间的会话工厂：只用于全局表，或由调用方在事务里自行 set_workspace。

    普通账号下用它读行级安全表一行都看不到（策略不成立），这是有意的失败即拒绝。
    """
    return async_sessionmaker(engine, expire_on_commit=False)


def make_workspace_sessionmaker(engine: AsyncEngine, workspace_id: str) -> async_sessionmaker:
    """每个事务开始时都声明工作空间，供行级安全判定（等价于 SET LOCAL，事务结束即失效）。

    开源版只有默认空间，对话、工具回执等模块用它即可；多空间时按请求选择对应的工作空间。

    注意：传入的引擎不能配置 AUTOCOMMIT 隔离级别。set_config(..., true) 只在当前事务内有效，
    自动提交下它所在的语句一结束设置就失效，之后的查询看不到工作空间，行级安全下静默读空。
    """
    if not workspace_id:
        raise ValueError("workspace_id is required")

    class WorkspaceSession(Session):
        pass

    @event.listens_for(WorkspaceSession, "after_begin")
    def _declare_workspace(session, transaction, connection) -> None:
        connection.execute(text("SELECT set_config(:name, :value, true)"),
                           {"name": WORKSPACE_SETTING, "value": workspace_id})

    return async_sessionmaker(engine, expire_on_commit=False, sync_session_class=WorkspaceSession)
