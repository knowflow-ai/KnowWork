"""会话附件（技术方案 8.2 节，F-CHAT-2）：上传、类型判定、MinerU 解析、放进沙箱 /workspace/input。

装配：
    attachments = build_attachments(sessionmaker, stores, sandbox)          # api 与 worker 各建一份
    app.include_router(attachments_router(attachments.service, conversation_service, actor))
    eraser = attachments.service.erase                                      # 交给 ConversationService(erasers=...)
    await attachments.worker.run_forever(lambda: ["default"], stop)         # worker 进程
    await attachments.aclose()
"""

from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..sandbox.contract import SessionSandbox
from ..sessionfiles.storage import SessionStores
from .api import build_router
from .mineru import MinerUClient, MinerUError, MinerUOptions, build_mineru_client
from .models import ATTACHMENT_RLS_TABLES, ATTACHMENT_TABLES, Attachment, AttachmentParseJob
from .service import AttachmentService, FileFailure, IncomingFile, UploadResult
from .settings import AttachmentSettings
from .worker import ParseWorker


@dataclass(frozen=True)
class Attachments:
    service: AttachmentService
    worker: ParseWorker
    mineru: MinerUClient | None
    http: httpx.AsyncClient | None

    async def aclose(self) -> None:
        if self.http is not None:
            await self.http.aclose()


def build_attachments(sessionmaker: async_sessionmaker, stores: SessionStores, sandbox: SessionSandbox,
                      settings: AttachmentSettings | None = None, *,
                      transport: httpx.AsyncBaseTransport | None = None) -> Attachments:
    settings = settings or AttachmentSettings()
    service = AttachmentService(sessionmaker, stores, sandbox, settings)
    mineru, http = None, None
    if settings.mineru_configured:
        options = MinerUOptions(backend=settings.mineru_backend, lang=settings.mineru_lang,
                                parse_timeout_seconds=settings.mineru_parse_timeout_seconds,
                                poll_interval_seconds=settings.mineru_poll_interval_seconds)
        mineru, http = build_mineru_client(settings.mineru_base_url, options,
                                           request_timeout=settings.mineru_request_timeout_seconds,
                                           transport=transport)
    worker = ParseWorker(sessionmaker, stores, sandbox, service, mineru, settings)
    return Attachments(service=service, worker=worker, mineru=mineru, http=http)


__all__ = [
    "ATTACHMENT_RLS_TABLES",
    "ATTACHMENT_TABLES",
    "Attachment",
    "AttachmentParseJob",
    "AttachmentService",
    "AttachmentSettings",
    "Attachments",
    "FileFailure",
    "IncomingFile",
    "MinerUClient",
    "MinerUError",
    "ParseWorker",
    "UploadResult",
    "build_attachments",
    "build_router",
]
