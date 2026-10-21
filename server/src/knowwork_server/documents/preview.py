"""产物预览：把 Word、PPT 转成 PDF 给前端的 PDF 阅读器，结果缓存在会话存储里。

- 转换在会话沙箱里做（断网、无凭证），业务服务只调度与缓存；会话容器被回收了就按需拉起；
- 同一产物并发预览只转一次（按会话与产物加锁），转换前后都先看缓存；
- 缓存对象丢了就重新转换，不报错。
"""

import asyncio
import weakref
from dataclasses import dataclass

from ..artifacts.collector import ArtifactView
from ..artifacts.service import ArtifactService
from ..conversations.models import Conversation
from ..sandbox.contract import SessionSandbox
from ..sandbox.docker_sandbox import CONVERTIBLE
from ..sessionfiles.names import extension

MIB = 1024 * 1024


class PreviewUnsupported(Exception):
    """这种文件不提供 PDF 预览（前端用原文件自己渲染，或只能下载）。"""


@dataclass(frozen=True)
class PreviewLimits:
    max_pdf_bytes: int = 50 * MIB
    timeout_seconds: float = 180.0


DEFAULT_LIMITS = PreviewLimits()


class PreviewService:
    def __init__(self, artifacts: ArtifactService, sandbox: SessionSandbox, limits: PreviewLimits = DEFAULT_LIMITS):
        self._artifacts = artifacts
        self._sandbox = sandbox
        self._limits = limits
        self._locks: weakref.WeakValueDictionary[tuple[str, str], asyncio.Lock] = weakref.WeakValueDictionary()

    def _lock(self, key: tuple[str, str]) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _cached(self, conversation: Conversation, artifact_id: str) -> bytes | None:
        digest = await self._artifacts.preview_digest(conversation, artifact_id)
        if digest is None:
            return None
        return await self._artifacts.read_preview(conversation, digest, max_bytes=self._limits.max_pdf_bytes)

    async def pdf(self, conversation: Conversation, artifact_id: str) -> tuple[ArtifactView, bytes]:
        """产物与它的 PDF 预览。产物不存在抛 ArtifactNotFound；不能转的类型抛 PreviewUnsupported；
        转换失败抛 SandboxError。"""
        content = await self._artifacts.open(conversation, artifact_id)
        ext = extension(content.artifact.filename)
        if ext == "pdf":
            return content.artifact, content.data
        if ext not in CONVERTIBLE:
            raise PreviewUnsupported(ext)
        async with self._lock((conversation.id, artifact_id)):
            cached = await self._cached(conversation, artifact_id)
            if cached is not None:
                return content.artifact, cached
            pdf = await self._sandbox.convert_to_pdf(conversation.workspace_id, conversation.id, ext, content.data,
                                                     max_bytes=self._limits.max_pdf_bytes,
                                                     timeout_seconds=self._limits.timeout_seconds)
            await self._artifacts.save_preview(conversation, artifact_id, pdf)
            return content.artifact, pdf
