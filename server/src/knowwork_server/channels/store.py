"""渠道的读写。sessionmaker 是按空间声明的那种（db.base.make_workspace_sessionmaker），调用方先用
in_workspace 进到渠道所在的空间；只有启动时列出全部空间要用不带行级安全的全局会话（workspace 表本身是全局的）。
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.models import Workspace
from ..conversations.models import Conversation, RunEvent
from ..db.base import in_workspace
from .models import Channel, ChannelSession, ChannelStranger

MAX_ERROR = 500
MAX_NAME = 100
MAX_SESSIONS = 200
WEB = "web"  # 网页渠道的接入方式（channel.platform）


@dataclass(frozen=True)
class SessionRow:
    session_key: str
    display_name: str
    conversation_id: str
    title: str
    updated_at: datetime
    user_id: str = ""


@dataclass(frozen=True)
class StrangerRow:
    external_id: str
    display_name: str
    reason: str
    attempts: int
    last_at: datetime


class ChannelStore:
    def __init__(self, sessionmaker: async_sessionmaker, global_sessionmaker: async_sessionmaker) -> None:
        self._sessionmaker = sessionmaker
        self._global = global_sessionmaker

    # ---- 渠道 ----

    async def runnable(self) -> list[Channel]:
        """全部空间里启用的渠道（关闭了的空间不算），启动时连上它们。"""
        async with self._global() as session:
            workspaces = (await session.scalars(select(Workspace.id).where(Workspace.deleted_at.is_(None)))).all()
        found: list[Channel] = []
        for workspace_id in workspaces:
            with in_workspace(workspace_id):
                async with self._sessionmaker() as session:
                    found += (await session.scalars(select(Channel).where(
                        Channel.workspace_id == workspace_id, Channel.enabled.is_(True)))).all()
        return found

    async def of_workspace(self, workspace_id: str) -> Sequence[Channel]:
        async with self._sessionmaker() as session:
            return (await session.scalars(select(Channel).where(Channel.workspace_id == workspace_id)
                                          .order_by(Channel.created_at))).all()

    async def get(self, workspace_id: str, channel_id: str) -> Channel | None:
        async with self._sessionmaker() as session:
            return (await session.scalars(select(Channel).where(
                Channel.workspace_id == workspace_id, Channel.id == channel_id))).first()

    async def by_web_key(self, workspace_id: str, web_key: str) -> Channel | None:
        """网页渠道按发布令牌找（令牌里带着空间，调用方先进到那个空间）。"""
        async with self._sessionmaker() as session:
            return (await session.scalars(select(Channel).where(
                Channel.workspace_id == workspace_id, Channel.platform == WEB,
                Channel.public_config["web_key"].astext == web_key))).first()

    async def channel_of_conversation(self, workspace_id: str, conversation_id: str) -> str | None:
        """这段会话是从哪个渠道来的；不是从渠道来的为空。"""
        async with self._sessionmaker() as session:
            return await session.scalar(select(Conversation.channel_id).where(
                Conversation.workspace_id == workspace_id, Conversation.id == conversation_id))

    async def create(self, channel: Channel) -> Channel:
        channel.id = channel.id or f"ch_{uuid.uuid4().hex[:20]}"
        async with self._sessionmaker() as session, session.begin():
            session.add(channel)
        return channel

    async def save(self, workspace_id: str, channel_id: str, **values) -> Channel | None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(update(Channel).where(
                Channel.workspace_id == workspace_id, Channel.id == channel_id).values(**values, updated_at=func.now()))
        return await self.get(workspace_id, channel_id)

    async def delete(self, workspace_id: str, channel_id: str) -> bool:
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(delete(Channel).where(
                Channel.workspace_id == workspace_id, Channel.id == channel_id))
        return bool(result.rowcount)

    async def set_status(self, workspace_id: str, channel_id: str, status: str, error: str = "") -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(update(Channel).where(
                Channel.workspace_id == workspace_id, Channel.id == channel_id).values(
                status=status, last_error=error[:MAX_ERROR], status_at=datetime.now(UTC)))

    # ---- 对话位置 ----

    async def conversation_of(self, workspace_id: str, channel_id: str, key: str, user_id: str) -> str | None:
        """这个对话位置当前对应的会话；会话不属于渠道账号了（不该发生，防一手）就当没有。"""
        async with self._sessionmaker() as session:
            return await session.scalar(select(ChannelSession.conversation_id)
                                        .join(Conversation, Conversation.id == ChannelSession.conversation_id)
                                        .where(ChannelSession.workspace_id == workspace_id,
                                               ChannelSession.channel_id == channel_id,
                                               ChannelSession.session_key == key,
                                               Conversation.user_id == user_id))

    async def remember(self, workspace_id: str, channel_id: str, key: str, conversation_id: str, user_id: str,
                       display_name: str) -> None:
        name = display_name[:MAX_NAME]
        statement = insert(ChannelSession).values(channel_id=channel_id, session_key=key, workspace_id=workspace_id,
                                                  conversation_id=conversation_id, user_id=user_id, display_name=name)
        async with self._sessionmaker() as session, session.begin():
            await session.execute(statement.on_conflict_do_update(
                index_elements=["channel_id", "session_key"],
                set_={"conversation_id": conversation_id, "user_id": user_id, "display_name": name,
                      "updated_at": func.now()}))

    async def touch(self, workspace_id: str, channel_id: str, key: str, display_name: str) -> None:
        """答完一条记一下时间；平台上改了名字的，顺手换成新名字（取不到名字时不覆盖）。"""
        values: dict = {"updated_at": func.now()}
        if display_name:
            values["display_name"] = display_name[:MAX_NAME]
        async with self._sessionmaker() as session, session.begin():
            await session.execute(update(ChannelSession).where(
                ChannelSession.workspace_id == workspace_id, ChannelSession.channel_id == channel_id,
                ChannelSession.session_key == key).values(**values))

    async def sessions(self, workspace_id: str, channel_id: str, limit: int = MAX_SESSIONS) -> list[SessionRow]:
        """最近在用的对话位置，新的在前：管理员看谁在问、在私聊还是哪个群。"""
        async with self._sessionmaker() as session:
            rows = (await session.execute(
                select(ChannelSession.session_key, ChannelSession.display_name, ChannelSession.conversation_id,
                       Conversation.title, ChannelSession.updated_at, ChannelSession.user_id)
                .join(Conversation, Conversation.id == ChannelSession.conversation_id)
                .where(ChannelSession.workspace_id == workspace_id, ChannelSession.channel_id == channel_id)
                .order_by(ChannelSession.updated_at.desc()).limit(limit))).all()
        return [SessionRow(*row) for row in rows]

    # ---- 没认出的人 ----

    async def stranger(self, workspace_id: str, channel_id: str, external_id: str, display_name: str,
                       reason: str) -> None:
        """记下私聊了却没认出来的人：同一个人再来只加次数、更新原因与时间。"""
        name = display_name[:MAX_NAME]
        statement = insert(ChannelStranger).values(channel_id=channel_id, external_id=external_id[:255],
                                                   workspace_id=workspace_id, display_name=name, reason=reason,
                                                   attempts=1)
        async with self._sessionmaker() as session, session.begin():
            await session.execute(statement.on_conflict_do_update(
                index_elements=["channel_id", "external_id"],
                set_={"display_name": name, "reason": reason, "attempts": ChannelStranger.attempts + 1,
                      "last_at": func.now()}))

    async def recognized(self, workspace_id: str, channel_id: str, external_id: str) -> None:
        """认出来了：从没认出的人里删掉。"""
        async with self._sessionmaker() as session, session.begin():
            await session.execute(delete(ChannelStranger).where(
                ChannelStranger.workspace_id == workspace_id, ChannelStranger.channel_id == channel_id,
                ChannelStranger.external_id == external_id))

    async def strangers(self, workspace_id: str, channel_id: str, limit: int = MAX_SESSIONS) -> list[StrangerRow]:
        async with self._sessionmaker() as session:
            rows = (await session.execute(
                select(ChannelStranger.external_id, ChannelStranger.display_name, ChannelStranger.reason,
                       ChannelStranger.attempts, ChannelStranger.last_at)
                .where(ChannelStranger.workspace_id == workspace_id, ChannelStranger.channel_id == channel_id)
                .order_by(ChannelStranger.last_at.desc()).limit(limit))).all()
        return [StrangerRow(*row) for row in rows]

    async def session_counts(self, workspace_id: str) -> dict[str, int]:
        async with self._sessionmaker() as session:
            rows = (await session.execute(select(ChannelSession.channel_id, func.count()).where(
                ChannelSession.workspace_id == workspace_id).group_by(ChannelSession.channel_id))).all()
        return dict(rows)

    async def forget(self, workspace_id: str, channel_id: str, key: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(delete(ChannelSession).where(
                ChannelSession.workspace_id == workspace_id, ChannelSession.channel_id == channel_id,
                ChannelSession.session_key == key))

    async def last_seq(self, conversation_id: str) -> int:
        """会话里已落库的最后一个事件序号：发这一轮之前记下，只看之后的事件。"""
        async with self._sessionmaker() as session:
            return await session.scalar(select(func.coalesce(func.max(RunEvent.seq), 0)).where(
                RunEvent.conversation_id == conversation_id)) or 0
