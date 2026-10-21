"""审计查询：按时间倒序、键集分页（游标 = 最后一条的时间与 id），按时间范围、操作者、动作、结果过滤。

不用页码加 OFFSET：审计表只增不改，翻到后面 OFFSET 越来越慢，期间新写入的记录还会让页面错位。
"""

import base64
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from .models import AuditLog

MAX_LIMIT = 200


class InvalidCursor(ValueError):
    pass


@dataclass(frozen=True)
class AuditFilter:
    since: datetime | None = None
    until: datetime | None = None
    actor_id: str | None = None
    actor_type: str | None = None
    action: str | None = None  # 末尾写 .* 表示前缀，例如 api_key.*
    result: str | None = None


@dataclass(frozen=True)
class AuditPage:
    items: tuple[AuditLog, ...]
    next: str | None


def encode_cursor(row: AuditLog) -> str:
    raw = f"{row.occurred_at.isoformat()}|{row.id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        stamp, _, row_id = raw.partition("|")
        moment = datetime.fromisoformat(stamp)
    except (ValueError, UnicodeDecodeError) as error:
        raise InvalidCursor("游标无效") from error
    if not row_id or moment.tzinfo is None:
        raise InvalidCursor("游标无效")
    return moment, row_id


def _conditions(workspace_id: str, flt: AuditFilter) -> list:
    conditions = [AuditLog.workspace_id == workspace_id]
    if flt.since is not None:
        conditions.append(AuditLog.occurred_at >= flt.since)
    if flt.until is not None:
        conditions.append(AuditLog.occurred_at < flt.until)
    if flt.actor_id:
        conditions.append(AuditLog.actor_id == flt.actor_id)
    if flt.actor_type:
        conditions.append(AuditLog.actor_type == flt.actor_type)
    if flt.result:
        conditions.append(AuditLog.result == flt.result)
    if flt.action and flt.action.endswith(".*"):
        prefix = flt.action[:-1].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(AuditLog.action.like(f"{prefix}%", escape="\\"))
    elif flt.action:
        conditions.append(AuditLog.action == flt.action)
    return conditions


class AuditQuery:
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def page(self, workspace_id: str, flt: AuditFilter, *, limit: int = 50,
                   cursor: str | None = None) -> AuditPage:
        limit = max(1, min(limit, MAX_LIMIT))
        conditions = _conditions(workspace_id, flt)
        if cursor:
            moment, row_id = decode_cursor(cursor)
            conditions.append(or_(AuditLog.occurred_at < moment,
                                  and_(AuditLog.occurred_at == moment, AuditLog.id < row_id)))
        statement = (select(AuditLog).where(*conditions)
                     .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc()).limit(limit + 1))
        async with self._sessionmaker() as session:
            await set_workspace(session, workspace_id)
            rows = list((await session.execute(statement)).scalars())
        items = tuple(rows[:limit])
        return AuditPage(items=items, next=encode_cursor(items[-1]) if len(rows) > limit else None)
