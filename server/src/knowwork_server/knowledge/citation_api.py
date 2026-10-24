"""引用出处的公开接口：按会话列出引用目录里的证据，前端点开回答里的 [n] 时用。

- 只有会话本人能看（同产物下载，不是本人或不存在一律 404）；
- 只给展示需要的字段：标题、原文摘录（截短）、页码、打开原文的链接、定位（页码、框、引用原文，预览里高亮用；
  片段横跨好几块时另给每块的定位，前端按引用的那句话挑）、
  片段里的图片（签过名的取图地址与描述，回答里的 [IMG:n:j] 按它换成图片）；完整原文留在服务端。
  预览文件由前端按知识源去取（引用预览插槽），取的时候按当前权限重新判定；
- 引用目录按会话划分（knowledge.tools 的 catalog_of 默认取会话 ID），编号在会话内唯一。
"""

from collections.abc import Callable

from fastapi import APIRouter, Depends

from ..conversations.api import Actor, ActorDependency
from ..sessionfiles.lookup import ConversationLookup, owned_conversation
from .citation_store import CitationStore
from .citations import CitationEntry
from .links import LinkSigner

EXCERPT_LIMIT = 600


def _excerpt(text: str) -> str:
    clean = " ".join(text.split())
    return clean if len(clean) <= EXCERPT_LIMIT else clean[:EXCERPT_LIMIT] + "…"


def citation_view(entry: CitationEntry, sign: Callable[[str], str] | None = None) -> dict:
    """sign：给站内取图地址加签名（<img> 发不了自定义请求头，靠签名认人）。不给就不带图片。"""
    evidence = entry.evidence
    return {
        "number": entry.number,
        "title": evidence.title,
        "excerpt": _excerpt(evidence.text),
        "page": evidence.locator.page if evidence.locator else None,
        "openUrl": evidence.open_url,
        "sourceId": evidence.source_id,
        "docRef": evidence.doc_ref,
        "locator": evidence.locator.model_dump(mode="json", exclude_defaults=True) if evidence.locator else None,
        # 这一块里每处的位置与原文：前端点开时整块高亮，回答里原样写了某处原文时只标那几处
        "locators": [loc.model_dump(mode="json", exclude_defaults=True) for loc in evidence.locators],
        "images": [{"url": sign(image.url), "caption": image.caption}
                   for image in evidence.images if image.url] if sign else [],
    }


def build_router(citations: CitationStore, conversations: ConversationLookup,
                 current_actor: ActorDependency, links: LinkSigner | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/conversations")

    @router.get("/{conversation_id}/citations")
    async def list_citations(conversation_id: str, who: Actor = Depends(current_actor)):  # noqa: B008
        conversation = await owned_conversation(conversations, conversation_id, who)
        catalog = await citations.load(conversation.workspace_id, conversation.id)
        sign = (lambda url: links.sign(url, who.user_id)) if links else None
        return {"items": [citation_view(entry, sign) for entry in catalog.entries],
                "truncated": catalog.truncated, "dropped": catalog.dropped}

    return router
