"""引用出处的公开接口：按会话列出引用目录里的证据，前端点开回答里的 [n] 时用。

- 只有会话本人能看（同产物下载，不是本人或不存在一律 404）；
- 只给展示需要的字段：标题、原文摘录（截短）、页码、打开原文的链接；完整原文与坐标框留在服务端；
- 引用目录按会话划分（knowledge.tools 的 catalog_of 默认取会话 ID），编号在会话内唯一。
"""

from fastapi import APIRouter, Depends

from ..conversations.api import Actor, ActorDependency
from ..sessionfiles.lookup import ConversationLookup, owned_conversation
from .citation_store import CitationStore
from .citations import CitationEntry

EXCERPT_LIMIT = 600


def _excerpt(text: str) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= EXCERPT_LIMIT else clean[:EXCERPT_LIMIT] + "…"


def citation_view(entry: CitationEntry) -> dict:
    evidence = entry.evidence
    return {
        "number": entry.number,
        "title": evidence.title,
        "excerpt": _excerpt(evidence.text),
        "page": evidence.locator.page if evidence.locator else None,
        "openUrl": evidence.open_url,
        "sourceId": evidence.source_id,
        "docRef": evidence.doc_ref,
    }


def build_router(citations: CitationStore, conversations: ConversationLookup,
                 current_actor: ActorDependency) -> APIRouter:
    router = APIRouter(prefix="/api/conversations")

    @router.get("/{conversation_id}/citations")
    async def list_citations(conversation_id: str, who: Actor = Depends(current_actor)):  # noqa: B008
        conversation = await owned_conversation(conversations, conversation_id, who)
        catalog = await citations.load(conversation.workspace_id, conversation.id)
        return {"items": [citation_view(entry) for entry in catalog.entries],
                "truncated": catalog.truncated, "dropped": catalog.dropped}

    return router
