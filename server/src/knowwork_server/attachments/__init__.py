"""会话附件：上传、类型判定、MinerU 解析、放进沙箱 /workspace/input。

装配：
    attachments = build_attachments(sessionmaker, stores, sandbox, parsers=parsers)  # api 与 worker 各建一份
    app.include_router(attachments_router(attachments.service, conversation_service, actor))
    eraser = attachments.service.erase                                      # 交给 ConversationService(erasers=...)
    attachments.parse_job.register(runner)                                  # worker 进程，runner 是 jobs.JobRunner
    await attachments.aclose()
"""

from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..sandbox.contract import SessionSandbox
from ..sessionfiles.storage import SessionStores
from .api import build_router
from .mineru import (
    MinerUClient,
    MinerUError,
    MinerUOptions,
    MinerUProvider,
    ParserSettingsSource,
)
from .models import ATTACHMENT_RLS_TABLES, ATTACHMENT_TABLES, Attachment
from .parse_job import ParseJob, parse_kind
from .service import AttachmentService, FileFailure, IncomingFile, UploadResult
from .settings import AttachmentSettings


@dataclass(frozen=True)
class Attachments:
    service: AttachmentService
    parse_job: ParseJob
    mineru: MinerUProvider

    async def aclose(self) -> None:
        await self.mineru.aclose()


def build_attachments(sessionmaker: async_sessionmaker, stores: SessionStores, sandbox: SessionSandbox,
                      settings: AttachmentSettings | None = None, *, parsers: ParserSettingsSource,
                      transport: httpx.AsyncBaseTransport | None = None, gate=None) -> Attachments:
    """parsers 是解析服务的运行时配置（管理后台「模型 → 文档解析」，docparse.DocumentParsers；后台没配时它退回
    环境变量）。gate 给了时 MinerU 的调用先过闸门（worker 里给；业务服务进程不调 MinerU）。"""
    settings = settings or AttachmentSettings()
    mineru = MinerUProvider(parsers, gate,
                            request_timeout=settings.mineru_request_timeout_seconds, transport=transport)
    service = AttachmentService(sessionmaker, stores, sandbox, settings, parse_kind(settings),
                                parser_configured=mineru.configured)
    options = MinerUOptions(backend=settings.mineru_backend, lang=settings.mineru_lang,
                            parse_timeout_seconds=settings.mineru_parse_timeout_seconds,
                            poll_interval_seconds=settings.mineru_poll_interval_seconds)
    parse_job = ParseJob(service, stores, sandbox, mineru, options, settings)
    return Attachments(service=service, parse_job=parse_job, mineru=mineru)


__all__ = [
    "ATTACHMENT_RLS_TABLES",
    "ATTACHMENT_TABLES",
    "Attachment",
    "AttachmentService",
    "AttachmentSettings",
    "Attachments",
    "FileFailure",
    "IncomingFile",
    "MinerUClient",
    "MinerUError",
    "MinerUOptions",
    "MinerUProvider",
    "ParseJob",
    "ParserSettingsSource",
    "UploadResult",
    "build_attachments",
    "build_router",
]
