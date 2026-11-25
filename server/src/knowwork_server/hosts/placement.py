"""对话去处的存放（session_host 表）。定了就不改：一段对话的官方会话日志只在一个宿主上。"""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from .launcher import HostKey
from .models import SessionHost


class StoredPlacement:
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sessions = sessionmaker

    async def _row(self, session_id: str) -> SessionHost | None:
        async with self._sessions() as session:
            return await session.scalar(select(SessionHost).where(SessionHost.session_id == session_id))

    async def get(self, session_id: str) -> HostKey | None:
        row = await self._row(session_id)
        if row is None or not row.host_user or not row.host_workspace:
            return None
        return HostKey(row.host_workspace, row.host_user)

    async def decided(self, session_id: str) -> bool:
        return await self._row(session_id) is not None

    async def put(self, session_id: str, workspace_id: str, key: HostKey | None) -> None:
        """先到先得：两个进程同时给同一段对话定去处时，后到的不覆盖（随后读到的是先到的那个）。"""
        async with self._sessions.begin() as session:
            await session.execute(
                insert(SessionHost).values(session_id=session_id, host_workspace=key.workspace_id if key else None,
                                           host_user=key.user_id if key else None).on_conflict_do_nothing())
