"""问答大盘：每轮汇总的聚合，给空间所有者和管理员看（见 service.py）。

主线整合：
    insights = build_insights(scoped_sessionmaker, access, timezone=settings.usage_timezone, base_names=...)
    app.include_router(build_router(insights, principal))
"""

from sqlalchemy.ext.asyncio import async_sessionmaker

from .api import build_router
from .service import AccessChecker, BaseNames, InsightsService

__all__ = ["InsightsService", "build_insights", "build_router"]


def build_insights(sessionmaker: async_sessionmaker, access: AccessChecker, *, timezone: str,
                   base_names: BaseNames | None = None) -> InsightsService:
    """sessionmaker 要声明好工作空间（同运行记录）；base_names 给了就把知识库全局 id 换成名字。"""
    return InsightsService(sessionmaker, access, timezone=timezone, base_names=base_names)
