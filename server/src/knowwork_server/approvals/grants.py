"""会话审批授权的记与查。

- 记：用户批准时勾了「本会话内同类操作不再询问」，按对话当前绑定的运行配置版本记一条（同一范围重复记只留一条）；
  对话还没绑定运行配置（没装配编译器的部署）时不记，下次照常问；
- 查：宿主审批应答者在弹卡片前来问，只认同一个人、同一个对话、同一个工具、同一版运行配置。查询出错由调用方
  当作没有授权（多问一次，不是放行）。
"""

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from .models import ApprovalGrant


@dataclass(frozen=True)
class ConversationRef:
    workspace_id: str
    id: str


VersionOf = Callable[[ConversationRef], Awaitable[str | None]]


class SessionGrants:
    def __init__(self, sessionmaker: async_sessionmaker, version_of: VersionOf) -> None:
        self._sessionmaker = sessionmaker
        self._version_of = version_of

    async def remember(self, workspace_id: str, conversation_id: str, user_id: str, tool_name: str) -> bool:
        version = await self._version_of(ConversationRef(workspace_id, conversation_id))
        if version is None:
            return False
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            await session.execute(insert(ApprovalGrant).values(
                id="ag_" + secrets.token_hex(8), workspace_id=workspace_id, conversation_id=conversation_id,
                user_id=user_id, tool_name=tool_name, profile_version=version,
            ).on_conflict_do_nothing(constraint="uk_approval_grant_scope"))
        return True

    async def covers(self, workspace_id: str, conversation_id: str, user_id: str, tool_name: str) -> bool:
        version = await self._version_of(ConversationRef(workspace_id, conversation_id))
        if version is None:
            return False
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            found = await session.scalar(select(ApprovalGrant.id).where(
                ApprovalGrant.conversation_id == conversation_id, ApprovalGrant.user_id == user_id,
                ApprovalGrant.tool_name == tool_name, ApprovalGrant.profile_version == version))
        return found is not None
