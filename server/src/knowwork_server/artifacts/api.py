"""产物接口：列表与下载。

- 每次请求都按当前登录用户确认会话是本人的（会话删除后记录随之删除，立即 404）；
- 一律以附件方式下载（Content-Disposition: attachment，中文名走 filename*），不内联返回；
  HTML、SVG、脚本这类会在浏览器里执行的类型再改报 application/octet-stream；
- 加 nosniff 与 CSP sandbox，即使被人直接在地址栏打开也不执行；不缓存。
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response

from ..conversations.api import Actor, ActorDependency
from ..sessionfiles.lookup import ConversationLookup, owned_conversation
from ..sessionfiles.names import content_disposition, extension
from .collector import ArtifactView
from .service import ArtifactNotFound, ArtifactService, ArtifactUnavailable

ARTIFACT_ID = Path(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_]+$")
ACTIVE_TYPES = frozenset({"html", "htm", "xhtml", "svg", "xml", "js", "mjs", "xsl", "shtml", "mht", "mhtml"})
# 下载审计钩子（审计里的「产物下载」），由装配方接审计模块
DownloadHook = Callable[[Actor, ArtifactView], Awaitable[None]]
# 列表前等这个会话正在后台收的产物落库（ArtifactObserver.settle），由装配方接入
SettleHook = Callable[[str], Awaitable[None]]


def artifact_json(item: ArtifactView) -> dict:
    return {"id": item.id, "filename": item.filename, "contentType": item.content_type, "size": item.size,
            "runId": item.run_id, "sanitized": item.sanitized,
            "createdAt": item.created_at.isoformat() if item.created_at else None}


def download_headers(item: ArtifactView) -> dict[str, str]:
    return {"content-disposition": content_disposition(item.filename), "x-content-type-options": "nosniff",
            "content-security-policy": "sandbox; default-src 'none'", "cache-control": "private, no-store"}


def media_type(item: ArtifactView) -> str:
    return "application/octet-stream" if extension(item.filename) in ACTIVE_TYPES else item.content_type


def build_router(service: ArtifactService, conversations: ConversationLookup, current_actor: ActorDependency, *,
                 on_download: DownloadHook | None = None, settle: SettleHook | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/conversations")
    Who = Annotated[Actor, Depends(current_actor)]

    @router.get("/{conversation_id}/artifacts")
    async def list_artifacts(conversation_id: str, who: Who):
        conversation = await owned_conversation(conversations, conversation_id, who)
        if settle is not None:
            await settle(conversation_id)
        return {"items": [artifact_json(item) for item in await service.list(conversation)]}

    @router.get("/{conversation_id}/artifacts/{artifact_id}")
    async def download(conversation_id: str, who: Who, artifact_id: Annotated[str, ARTIFACT_ID]):
        conversation = await owned_conversation(conversations, conversation_id, who)
        try:
            content = await service.open(conversation, artifact_id)
        except ArtifactNotFound as error:
            raise HTTPException(status_code=404, detail="产物不存在") from error
        except ArtifactUnavailable as error:
            raise HTTPException(status_code=410, detail="产物内容已不可用") from error
        if on_download is not None:
            await on_download(who, content.artifact)  # 审计写失败则下载也失败
        return Response(content=content.data, media_type=media_type(content.artifact),
                        headers=download_headers(content.artifact))

    return router
