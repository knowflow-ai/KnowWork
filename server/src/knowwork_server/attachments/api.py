"""附件接口：上传（multipart，可多文件）与列表。会话必须属于当前调用者，否则 404。

上传的返回结构逐个列出成功与失败：items 是已收下的附件（含已放进沙箱或正在解析），
failures 是没收下或放不进沙箱的文件，每项带文件名、错误码和可直接展示的原因。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from ..conversations.api import Actor, ActorDependency
from ..sessionfiles.lookup import ConversationLookup, owned_conversation
from .service import AttachmentService, AttachmentView, FileFailure, IncomingFile

READ_CHUNK = 1024 * 1024


def attachment_view(item: AttachmentView) -> dict:
    return {"id": item.id, "filename": item.filename, "inputName": item.input_name, "kind": item.kind,
            "size": item.size, "status": item.status, "errorCode": item.error_code,
            "errorMessage": item.error_message, "parsedName": item.parsed_name, "parseStatus": item.parse_status,
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


def build_router(service: AttachmentService, conversations: ConversationLookup,
                 current_actor: ActorDependency) -> APIRouter:
    router = APIRouter(prefix="/api/conversations")
    Who = Annotated[Actor, Depends(current_actor)]

    @router.post("/{conversation_id}/attachments")
    async def upload(conversation_id: str, who: Who, files: Annotated[list[UploadFile], File()]):
        conversation = await owned_conversation(conversations, conversation_id, who)
        if len(files) > service.max_files:
            raise HTTPException(status_code=400, detail={"code": "too_many_files",
                                                         "message": f"一次最多上传 {service.max_files} 个文件"})
        incoming = []
        for upload_file in files:
            try:
                incoming.append(await read_limited(upload_file, service.max_bytes))
            finally:
                await upload_file.close()
        result = await service.upload(conversation, incoming)
        return {"items": [attachment_view(i) for i in result.items],
                "failures": [failure_view(f) for f in result.failures]}

    @router.get("/{conversation_id}/attachments")
    async def list_attachments(conversation_id: str, who: Who):
        conversation = await owned_conversation(conversations, conversation_id, who)
        return {"items": [attachment_view(i) for i in await service.list(conversation)]}

    return router
