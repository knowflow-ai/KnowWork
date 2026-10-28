"""运行调试视图：网页里逐步查看一轮的每一步——模型输出、工具调用与结果、失败与重试、
用量，以及按日志还原的输入；可导出原始日志。只用已有数据（会话日志 run_event、用量 usage_record），不接外部观测服务。
"""

from sqlalchemy.ext.asyncio import async_sessionmaker

from ..audit.sink import AuditSink
from ..conversations.runlog import RunLog
from ..conversations.summaries import SummaryQuery
from .admin import AccessChecker, ConversationFinder, ProfileLookup, RunRecords, TurnTotals
from .admin_api import build_admin_router
from .api import build_router
from .service import CallTraces, DebugService, UsageSource

__all__ = ["DebugService", "RunRecords", "build_admin_router", "build_debugview", "build_router", "build_run_records"]


def build_debugview(scoped_sessionmaker: async_sessionmaker, usage: UsageSource,
                    traces: CallTraces | None = None) -> DebugService:
    """scoped_sessionmaker 要声明好工作空间（run_event 开了行级安全），同会话回放用的那个。
    traces 给了就把知识检索这类工具调用的过程挂上（knowledge.search_log）。"""
    return DebugService(RunLog(scoped_sessionmaker), usage, traces)


def build_run_records(scoped_sessionmaker: async_sessionmaker, usage: TurnTotals, debug: DebugService,
                      conversations: ConversationFinder, access: AccessChecker, audit: AuditSink,
                      profiles: ProfileLookup | None = None) -> RunRecords:
    """管理端「运行记录」：列表读每轮汇总，详情读原始帧；profiles 给了就在列表里带上成员姓名。"""
    return RunRecords(SummaryQuery(scoped_sessionmaker), usage, debug, conversations, access, audit, profiles)
