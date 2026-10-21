"""预览接口：GET /api/conversations/{会话}/artifacts/{产物}/preview 返回 PDF（Word、PPT 转换后的，或 PDF 原件）。

- 每次请求都按当前登录用户确认会话是本人的，与下载同一口径；预览也能看到全文，所以同样记审计；
- 响应头同下载：附件方式、nosniff、CSP sandbox、不缓存。前端用 fetch 取字节交给 PDF 阅读器，不直接打开；
- 不能转换的类型 415（前端改用原文件渲染或只给下载），转换失败或沙箱不可用 503，说明可以直接给用户看。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from ..artifacts.api import ARTIFACT_ID, DownloadHook, download_headers
from ..artifacts.collector import ArtifactView
from ..artifacts.service import ArtifactNotFound, ArtifactUnavailable
from ..conversations.api import Actor, ActorDependency
from ..sandbox.contract import SandboxError
from ..sessionfiles.lookup import ConversationLookup, owned_conversation
from .preview import PreviewService, PreviewUnsupported


def build_router(preview: PreviewService, conversations: ConversationLookup, current_actor: ActorDependency, *,
                 on_view: DownloadHook | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/conversations")
    Who = Annotated[Actor, Depends(current_actor)]

    @router.get("/{conversation_id}/artifacts/{artifact_id}/preview")
    async def preview_pdf(conversation_id: str, who: Who, artifact_id: Annotated[str, ARTIFACT_ID]):
        conversation = await owned_conversation(conversations, conversation_id, who)
        try:
            artifact, pdf = await preview.pdf(conversation, artifact_id)
        except ArtifactNotFound as error:
            raise HTTPException(status_code=404, detail="产物不存在") from error
        except ArtifactUnavailable as error:
            raise HTTPException(status_code=410, detail="产物内容已不可用") from error
        except PreviewUnsupported as error:
            raise HTTPException(status_code=415, detail="这种文件不提供 PDF 预览") from error
        except SandboxError as error:
            raise HTTPException(status_code=503, detail=f"预览暂时不可用：{error}") from error
        if on_view is not None:
            await on_view(who, artifact)
        return Response(content=pdf, media_type="application/pdf", headers=download_headers(_as_pdf(artifact)))

    return router


def _as_pdf(artifact: ArtifactView) -> ArtifactView:
    stem = artifact.filename.rsplit(".", 1)[0] if "." in artifact.filename else artifact.filename
    return ArtifactView(id=artifact.id, filename=f"{stem}.pdf", content_type="application/pdf", size=artifact.size,
                        run_id=artifact.run_id, sanitized=artifact.sanitized, created_at=artifact.created_at)
