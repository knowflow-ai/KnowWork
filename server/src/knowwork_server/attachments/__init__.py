"""会话附件：上传（先暂存、发消息时认领进会话）、类型判定、MinerU 解析、放进沙箱 /workspace/input、到期清理。

装配：
    attachments = build_attachments(sessionmaker, stores, sandbox, parsers=parsers)  # api 与 worker 各建一份
    app.include_router(attachments_router(attachments.service, conversation_service, actor))
    eraser = attachments.service.erase                                      # 交给 ConversationService(erasers=...)
    attachments.parse_job.register(runner)                                  # worker 进程，runner 是 jobs.JobRunner
    attachments.expire_job.register(runner)
    await attachments.aclose()
"""

from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..sandbox.contract import SessionSandbox
from ..sessionfiles.storage import SessionStores
from .api import build_router
from .digest_job import ChatModel, DigestJob, digest_kind
from .expire_job import ExpireJob, expire_kind
from .mineru import (
    MinerUClient,
    MinerUError,
    MinerUOptions,
    MinerUProvider,
    ParserSettingsSource,
    attachment_options,
)
from .models import ATTACHMENT_RLS_TABLES, ATTACHMENT_TABLES, Attachment
from .parse_job import ParseJob, parse_kind
from .service import AttachmentService, FileFailure, IncomingFile, UploadResult
from .settings import AttachmentSettings


@dataclass(frozen=True)
class Attachments:
    service: AttachmentService
    parse_job: ParseJob
    expire_job: ExpireJob
    mineru: MinerUProvider
    stores: SessionStores
    sandbox: SessionSandbox
    settings: AttachmentSettings

    def digest_job(self, models: ChatModel) -> DigestJob:
        """导读任务要调模型，只在 worker 里建（模型客户端在那里）。"""
        return DigestJob(self.service, self.stores, self.sandbox, models, self.settings)

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
    service = AttachmentService(sessionmaker, stores, sandbox, settings, parse_kind(settings), expire_kind(),
                                digest_kind(), parser_configured=mineru.configured)
    parse_job = ParseJob(service, stores, sandbox, mineru, attachment_options(settings), settings)
    return Attachments(service=service, parse_job=parse_job, expire_job=ExpireJob(service), mineru=mineru,
                       stores=stores, sandbox=sandbox, settings=settings)


__all__ = [
    "ATTACHMENT_RLS_TABLES",
    "ATTACHMENT_TABLES",
    "Attachment",
    "AttachmentService",
    "AttachmentSettings",
    "Attachments",
    "ExpireJob",
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
