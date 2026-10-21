"""会话与配置版本的绑定：读上一版、记新版，以及给知识工具的本轮绑定范围。"""

import logging

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from ..tools.registry import ToolContext, ToolFailure
from .models import KernelBinding, RuntimeProfileRow
from .snapshot import RuntimeProfile

log = logging.getLogger(__name__)


class ProfileBindings:
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sessionmaker = sessionmaker

    async def version_of(self, workspace_id: str, conversation_id: str) -> str | None:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            return await session.scalar(select(KernelBinding.profile_version)
                                        .where(KernelBinding.conversation_id == conversation_id))

    async def bind(self, workspace_id: str, conversation_id: str, profile: RuntimeProfile) -> None:
        """存快照（同版本只存一次）并把会话指向它。"""
        version = profile.version
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            await session.execute(insert(RuntimeProfileRow).values(
                workspace_id=workspace_id, version=version, user_id=profile.user_id, snapshot=profile.payload(),
            ).on_conflict_do_nothing())
            await session.execute(insert(KernelBinding).values(
                conversation_id=conversation_id, workspace_id=workspace_id, profile_version=version,
            ).on_conflict_do_update(index_elements=[KernelBinding.conversation_id],
                                    set_={"profile_version": version, "workspace_id": workspace_id}))

    async def current(self, workspace_id: str, conversation_id: str) -> RuntimeProfile | None:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            row = await session.scalar(select(RuntimeProfileRow.snapshot).join(
                KernelBinding, (KernelBinding.workspace_id == RuntimeProfileRow.workspace_id)
                & (KernelBinding.profile_version == RuntimeProfileRow.version),
            ).where(KernelBinding.conversation_id == conversation_id))
        return None if row is None else RuntimeProfile.model_validate(row)

    async def erase(self, workspace_id: str, conversation_id: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            await session.execute(delete(KernelBinding).where(KernelBinding.conversation_id == conversation_id))

    async def bound_bases(self, ctx: ToolContext) -> tuple[str, ...] | None:
        """给 knowledge_tools(bound_bases=...)：本轮明确选了知识库时返回它们，否则 None（全部可查的）。

        子 Agent 的会话 id 与根会话不同，但工具令牌里的 session_id 是根会话，所以这里查得到。
        会话还没有绑定配置时返回 None，与现状一致；查询出错时拒绝这次调用（失败关闭），
        不能因为读不到绑定范围就放宽成全部知识库。
        """
        try:
            profile = await self.current(ctx.workspace_id, ctx.session_id)
        except Exception as error:
            log.exception("runtime profile lookup failed for %s", ctx.session_id)
            raise ToolFailure("暂时读不到本轮的知识范围，请稍后再试") from error
        return None if profile is None else profile.bound_knowledge
