"""产物的读取与清理。下载每次都由 API 层按当前登录用户确认会话归属，这里只按会话取产物。"""

import logging
from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from ..conversations.models import Conversation
from ..sessionfiles.storage import SessionStores
from ..skills.errors import BlobIntegrityError
from .collector import ArtifactView, artifact_view
from .models import Artifact

log = logging.getLogger(__name__)


class ArtifactNotFound(Exception):
    pass


class ArtifactUnavailable(Exception):
    """记录在，但内容读不出或校验不过。"""


@dataclass(frozen=True)
class ArtifactContent:
    artifact: ArtifactView
    data: bytes


class ArtifactService:
    def __init__(self, sessionmaker: async_sessionmaker, stores: SessionStores) -> None:
        self._sessionmaker = sessionmaker
        self._stores = stores

    async def _rows(self, conversation: Conversation, *conditions) -> list[Artifact]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, conversation.workspace_id)
            return list((await session.scalars(select(Artifact).where(
                Artifact.conversation_id == conversation.id, *conditions)
                .order_by(Artifact.created_at, Artifact.id))).all())

    async def list(self, conversation: Conversation) -> tuple[ArtifactView, ...]:
        return tuple(artifact_view(row) for row in await self._rows(conversation))

    async def open(self, conversation: Conversation, artifact_id: str) -> ArtifactContent:
        rows = await self._rows(conversation, Artifact.id == artifact_id)
        if not rows:
            raise ArtifactNotFound(artifact_id)
        row = rows[0]
        try:
            data = await self._stores(conversation.workspace_id, conversation.id).get(row.digest, max_bytes=row.size)
        except BlobIntegrityError as error:
            log.warning("artifact %s content unavailable", artifact_id)
            raise ArtifactUnavailable(artifact_id) from error
        return ArtifactContent(artifact=artifact_view(row), data=data)

    async def preview_digest(self, conversation: Conversation, artifact_id: str) -> str | None:
        rows = await self._rows(conversation, Artifact.id == artifact_id)
        return rows[0].preview_digest if rows else None

    async def read_preview(self, conversation: Conversation, digest: str, *, max_bytes: int) -> bytes | None:
        """取缓存的预览 PDF；对象丢了或校验不过返回 None，由调用方重新转换。"""
        try:
            return await self._stores(conversation.workspace_id, conversation.id).get(digest, max_bytes=max_bytes)
        except BlobIntegrityError:
            log.warning("cached preview %s of %s unavailable", digest, conversation.id)
            return None

    async def save_preview(self, conversation: Conversation, artifact_id: str, data: bytes) -> None:
        digest = await self._stores(conversation.workspace_id, conversation.id).put(data)
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, conversation.workspace_id)
            await session.execute(update(Artifact).where(Artifact.conversation_id == conversation.id,
                                                         Artifact.id == artifact_id).values(preview_digest=digest))

    async def erase(self, conversation: Conversation) -> None:
        """会话删除时调用：先删记录（立即不可下载），再删对象（含预览用的 PDF）。"""
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, conversation.workspace_id)
            rows = (await session.execute(select(Artifact.digest, Artifact.preview_digest)
                                          .where(Artifact.conversation_id == conversation.id))).all()
            digests = {digest for row in rows for digest in row if digest}
            await session.execute(delete(Artifact).where(Artifact.conversation_id == conversation.id))
        store = self._stores(conversation.workspace_id, conversation.id)
        for digest in digests:
            try:
                await store.delete(digest)
            except BlobIntegrityError:
                log.warning("failed to delete artifact object of %s", conversation.id)
