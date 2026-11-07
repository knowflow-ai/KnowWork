"""附件接口。

- 网页输入框：一选文件就传到 /attachments（暂存在本人名下，马上开始解析），按 id 查进度，移除时删掉；发消息时带上 id，
  那时才认领进会话（service.claim）。
- 按会话上传（/conversations/{id}/attachments，给接口调用方）：传上来直接认领进这个会话；会话必须属于当前调用者，否则 404。

上传的返回结构逐个列出成功与失败：items 是已收下的附件（含已放进沙箱或正在解析），
failures 是没收下或放不进沙箱的文件，每项带文件名、错误码和可直接展示的原因。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile

from ..conversations.api import Actor, ActorDependency
from ..sessionfiles.lookup import ConversationLookup, owned_conversation
from .service import ALREADY_SENT, AttachmentService, AttachmentView, FileFailure, IncomingFile
from .types import AttachmentRejected

READ_CHUNK = 1024 * 1024
MAX_STATE_IDS = 20


def attachment_view(item: AttachmentView) -> dict:
    return {"id": item.id, "filename": item.filename, "inputName": item.input_name, "kind": item.kind,
            "size": item.size, "status": item.status, "errorCode": item.error_code,
            "errorMessage": item.error_message, "parsedName": item.parsed_name, "parseStatus": item.parse_status,
            "digestStatus": item.digest_status, "digestDone": item.digest_done, "digestTotal": item.digest_total,
            "createdAt": item.created_at.isoformat() if item.created_at else None}


def failure_view(item: FileFailure) -> dict:
    return {"filename": item.filename, "code": item.code, "message": item.message}


async def read_limited(upload: UploadFile, limit: int) -> IncomingFile:
    """按块读，超过上限立即停：不把超大文件整个读进内存。"""
    chunks, total = [], 0
    while chunk := await upload.read(READ_CHUNK):
        total += len(chunk)
        if total > limit:
            return IncomingFile(filename=upload.filename, data=b"", too_large=True)
        chunks.append(chunk)
    return IncomingFile(filename=upload.filename, data=b"".join(chunks))


def result_view(items, failures) -> dict:
    return {"items": [attachment_view(i) for i in items], "failures": [failure_view(f) for f in failures]}


async def read_all(service: AttachmentService, files: list[UploadFile]) -> list[IncomingFile]:
    if len(files) > service.max_files:
        raise HTTPException(status_code=400, detail={"code": "too_many_files",
                                                     "message": f"一次最多上传 {service.max_files} 个文件"})
    incoming = []
    for upload_file in files:
        try:
            incoming.append(await read_limited(upload_file, service.max_bytes))
        finally:
            await upload_file.close()
    return incoming


def build_router(service: AttachmentService, conversations: ConversationLookup,
                 current_actor: ActorDependency) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}")
    Who = Annotated[Actor, Depends(current_actor)]

    @router.post("/attachments")
    async def stage(who: Who, files: Annotated[list[UploadFile], File()]):
        """暂存附件（发消息前先传）

        还没有会话也能传（新对话的输入框里一选文件就传）；要解析的马上开始解析。发消息时把 id 放进 attachments，
        那时才放进会话。一直没发出去的到期删除。"""
        result = await service.stage(who.workspace_id, who.user_id, await read_all(service, files))
        return result_view(result.items, result.failures)

    @router.get("/attachments")
    async def states(who: Who, ids: Annotated[str, Query(min_length=1, max_length=2000)]):
        """查附件进度

        ids 用逗号分隔，最多 20 个；只返回本人的。输入框等解析完再发消息时用它。"""
        wanted = [i for i in ids.split(",") if i][:MAX_STATE_IDS]
        return {"items": [attachment_view(i) for i in await service.states(who.workspace_id, who.user_id, wanted)]}

    @router.delete("/attachments/{attachment_id}", status_code=204)
    async def discard(attachment_id: str, who: Who):
        """移除没发出去的附件

        已经发出去的随会话一起删，这里不能单独删（409）。"""
        try:
            await service.discard(who.workspace_id, who.user_id, attachment_id)
        except AttachmentRejected as error:
            status = 409 if error.code == ALREADY_SENT[0] else 404
            raise HTTPException(status_code=status, detail={"code": error.code, "message": error.message}) from error
        return Response(status_code=204)

    @router.post("/conversations/{conversation_id}/attachments")
    async def upload(conversation_id: str, who: Who, files: Annotated[list[UploadFile], File()]):
        """上传附件到会话

        给接口调用方：传上来直接放进这个会话。网页走「暂存附件」，发消息时才放进会话。"""
        conversation = await owned_conversation(conversations, conversation_id, who)
        result = await service.upload(conversation, await read_all(service, files))
        return result_view(result.items, result.failures)

    @router.get("/conversations/{conversation_id}/attachments")
    async def list_attachments(conversation_id: str, who: Who):
        """列出附件"""
        conversation = await owned_conversation(conversations, conversation_id, who)
        return {"items": [attachment_view(i) for i in await service.list(conversation)]}

    return router
