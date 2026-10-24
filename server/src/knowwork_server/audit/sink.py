"""AuditSink：审计事件的去处。默认写库并保留 90 天；扩展可以另外导出与外发。

调用方一律经 safe_record 写入：sink 抛什么异常都只记日志，不影响业务请求。
扩展可以用 FanoutSink 把写库与外发组合起来，任何一路失败不影响其他路。
"""

import logging
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from .events import AuditEvent
from .writer import AuditWriter

log = logging.getLogger(__name__)


@runtime_checkable
class AuditSink(Protocol):
    async def record(self, event: AuditEvent) -> None: ...


class DatabaseAuditSink:
    """开源版实现：交给批量写入器，入队即返回。"""

    def __init__(self, writer: AuditWriter) -> None:
        self._writer = writer

    async def record(self, event: AuditEvent) -> None:
        self._writer.put(event)


class FanoutSink:
    def __init__(self, sinks: Iterable[AuditSink]) -> None:
        self._sinks = tuple(sinks)

    async def record(self, event: AuditEvent) -> None:
        for sink in self._sinks:
            await safe_record(sink, event)


class NullSink:
    async def record(self, event: AuditEvent) -> None:
        return None


async def safe_record(sink: AuditSink | None, event: AuditEvent) -> None:
    if sink is None:
        return
    try:
        await sink.record(event)
    except Exception:
        log.exception("audit sink failed for action=%s workspace=%s", event.action, event.workspace_id)
