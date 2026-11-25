"""定期清理专属宿主留下的持久数据。

每人一个持久目录（容器起法是具名卷），放会话日志和插件存在主目录里的数据：宿主空闲回收以后它还留着，下次再起接得上。
不清的话只增不减。两种情况删：

- 这个人已经不在那个空间了（被移出、空间删了）：他的对话本来就看不到了；
- 很久没用了：他在专属宿主上的对话，最后一次有动静距今超过保留天数（KNOWWORK_PLUGIN_HOST_RETENTION_DAYS，默认 90 天，
  0 表示不按时间删）。对话都删光了的，按最后一段对话落到专属宿主上的时间算。删了以后他那些旧对话还能翻看（历史在业务服务里），
  但接不着聊了（宿主上的会话日志没了），要新开对话；插件存的数据也没了。

宿主正在跑（或正在建）的不删，用的是和建宿主同一把锁（DedicatedHosts.remove_home）。删完把这个人的对话去处记录一起删掉，
下一轮就不再看他。在 api 进程里跑：宿主的登记和锁都在 api 进程的内存里；活很轻，几小时跑一次。
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..conversations.models import Conversation
from ..db.base import in_workspace
from .dedicated import DedicatedHosts
from .launcher import HostKey
from .models import SessionHost

log = logging.getLogger(__name__)

Member = Callable[[str, str], bool]  # (user_id, workspace_id) → 还是不是这个空间的成员


class HomeCleanup:
    def __init__(self, hosts: DedicatedHosts, sessionmaker: async_sessionmaker, scoped: async_sessionmaker, *,
                 member: Member, retention_days: int, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        """sessionmaker 不按空间（session_host 表不做行级安全）；scoped 按空间（读对话的最后动静）。"""
        self._hosts = hosts
        self._sessions = sessionmaker
        self._scoped = scoped
        self._member = member
        self._retention = timedelta(days=retention_days) if retention_days > 0 else None
        self._clock = clock

    async def _owners(self) -> list[tuple[HostKey, datetime]]:
        """有过专属宿主的人，和他最后一段对话落到专属宿主上的时间。"""
        async with self._sessions() as session:
            rows = await session.execute(
                select(SessionHost.host_workspace, SessionHost.host_user, func.max(SessionHost.created_at))
                .where(SessionHost.host_user.is_not(None), SessionHost.host_workspace.is_not(None))
                .group_by(SessionHost.host_workspace, SessionHost.host_user))
        return [(HostKey(workspace, user), placed) for workspace, user, placed in rows.all()]

    async def _last_used(self, key: HostKey, placed: datetime) -> datetime:
        """他在专属宿主上的对话最后一次有动静的时间（每发一轮都会刷新）；对话都删了就用最后一次落过来的时间。"""
        sessions = select(SessionHost.session_id).where(SessionHost.host_workspace == key.workspace_id,
                                                        SessionHost.host_user == key.user_id)
        with in_workspace(key.workspace_id):
            async with self._scoped() as session:
                latest = await session.scalar(select(func.max(Conversation.updated_at)).where(
                    Conversation.workspace_id == key.workspace_id, Conversation.id.in_(sessions)))
        return max(latest, placed) if latest is not None else placed

    async def _forget(self, key: HostKey) -> None:
        async with self._sessions.begin() as session:
            await session.execute(delete(SessionHost).where(SessionHost.host_workspace == key.workspace_id,
                                                            SessionHost.host_user == key.user_id))

    async def run_once(self) -> int:
        """清一轮，返回删了几个人的。一个人删不掉（Docker 暂时不通之类）不影响别人，下一轮再试。"""
        removed = 0
        cutoff = self._clock() - self._retention if self._retention is not None else None
        for key, placed in await self._owners():
            gone = not self._member(key.user_id, key.workspace_id)
            if not gone and (cutoff is None or await self._last_used(key, placed) >= cutoff):
                continue
            try:
                if not await self._hosts.remove_home(key):
                    continue  # 正在用
                await self._forget(key)
            except Exception:
                log.warning("could not remove the plugin host home of %s/%s", key.workspace_id, key.user_id,
                            exc_info=True)
                continue
            removed += 1
            log.info("removed the plugin host home of %s/%s (%s)", key.workspace_id, key.user_id,
                     "left the workspace" if gone else "unused")
        return removed
