"""预览接口：GET …/conversations/{会话}/artifacts/{产物}/preview 返回 PDF（Word、PPT 转换后的，或 PDF 原件）；
GET …/conversations/{会话}/files/content?path= 与 …/files/preview?path= 按路径读对话里的文件与它的 PDF（files.py）。

- 每次请求都按当前登录用户确认会话是本人的，与下载同一口径；预览也能看到全文，所以同样记审计；
- 响应头同下载：附件方式、nosniff、CSP sandbox、不缓存。前端用 fetch 取字节交给 PDF 阅读器，不直接打开；
- 不能转换的类型 415（前端改用原文件渲染或只给下载），转换失败或沙箱不可用 503，说明可以直接给用户看。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..artifacts.api import ARTIFACT_ID, DownloadHook, download_headers
from ..artifacts.collector import ArtifactView
from ..artifacts.service import ArtifactNotFound, ArtifactUnavailable
from ..conversations.api import Actor, ActorDependency
from ..sandbox.contract import SandboxError, SandboxTooLarge
from ..sessionfiles.lookup import ConversationLookup, owned_conversation
from ..sessionfiles.names import content_disposition
from .files import ConversationFiles, FileContent, FileGone, FileMissing, FileOutside, FilesOnDevice
from .preview import PreviewService, PreviewUnsupported

FILE_PATH = Query(min_length=1, max_length=4096, description="模型写的路径：相对路径、/workspace/... 或 /sessions/<会话>/...")


def _file_response(file: FileContent, media_type: str) -> Response:
    headers = {"content-disposition": content_disposition(file.name), "x-content-type-options": "nosniff",
               "content-security-policy": "sandbox; default-src 'none'", "cache-control": "private, no-store"}
    return Response(content=file.data, media_type=media_type, headers=headers)


async def _serve(load) -> FileContent:
    """把对话文件的失败换成给人看的说明。"""
    try:
        return await load()
    except FilesOnDevice as error:
        raise HTTPException(status_code=409, detail="这段对话在创建它的电脑上执行，文件在那台电脑上") from error
    except FileOutside as error:
        raise HTTPException(status_code=400, detail="只能读这段对话工作目录里的文件") from error
    except FileMissing as error:
        raise HTTPException(status_code=404, detail="找不到这个文件，可能已经移走或删掉了") from error
    except FileGone as error:
        raise HTTPException(status_code=410,
                            detail="文件已随执行环境回收，交付过的文件仍可在交付卡片里打开") from error
    except (ArtifactNotFound, ArtifactUnavailable) as error:
        raise HTTPException(status_code=410, detail="文件内容已不可用") from error
    except PreviewUnsupported as error:
        raise HTTPException(status_code=415, detail="这种文件不提供 PDF 预览") from error
    except SandboxTooLarge as error:
        raise HTTPException(status_code=413, detail="文件太大，不能在这里打开") from error
    except SandboxError as error:
        raise HTTPException(status_code=503, detail=f"文件暂时读不了：{error}") from error


def build_router(preview: PreviewService, conversations: ConversationLookup, current_actor: ActorDependency, *,
                 on_view: DownloadHook | None = None, files: ConversationFiles | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/conversations")
    Who = Annotated[Actor, Depends(current_actor)]

    @router.get("/{conversation_id}/artifacts/{artifact_id}/preview")
    async def preview_pdf(conversation_id: str, who: Who, artifact_id: Annotated[str, ARTIFACT_ID]):
        """预览产物文件"""
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

    if files is not None:

        @router.get("/{conversation_id}/files/content")
        async def file_content(conversation_id: str, who: Who, path: Annotated[str, FILE_PATH]):
            """读对话里的文件

            回答里的文件链接、交付卡片按路径读原件：交付过的读收好的那份，没交付过的从会话沙箱现读。
            """
            conversation = await owned_conversation(conversations, conversation_id, who)
            file = await _serve(lambda: files.read(conversation, path))
            return _file_response(file, "application/octet-stream")

        @router.get("/{conversation_id}/files/preview")
        async def file_preview(conversation_id: str, who: Who, path: Annotated[str, FILE_PATH]):
            """预览对话里的文件

            Word、PPT 等转成的 PDF，PDF 原样；交付过的复用产物预览的缓存。
            """
            conversation = await owned_conversation(conversations, conversation_id, who)
            file = await _serve(lambda: files.pdf(conversation, path))
            return _file_response(file, "application/pdf")

    return router


def _as_pdf(artifact: ArtifactView) -> ArtifactView:
    stem = artifact.filename.rsplit(".", 1)[0] if "." in artifact.filename else artifact.filename
    return ArtifactView(id=artifact.id, filename=f"{stem}.pdf", content_type="application/pdf", size=artifact.size,
                        run_id=artifact.run_id, sanitized=artifact.sanitized, created_at=artifact.created_at)
