"""引用出处的公开接口：按会话列出引用目录里的证据，前端点开回答里的 [n] 时用。

- 只有会话本人能看（同产物下载，不是本人或不存在一律 404）；
- 只给展示需要的字段：标题、原文摘录（截短、剥掉内嵌标签）、正文里的图（补全成绝对地址与图注，气泡里显示）、
  页码、打开原文的链接、定位（页码、框、引用原文，预览里高亮用；
  片段横跨好几块时另给每块的定位，前端按引用的那句话挑）、
  片段里的图片（签过名的取图地址与描述，回答里的 [IMG:n:j] 按它换成图片）；完整原文留在服务端。
  预览文件由前端按知识源去取（引用预览插槽），取的时候按当前权限重新判定；
- 引用目录按会话划分（knowledge.tools 的 catalog_of 默认取会话 ID），编号在会话内唯一。
"""

import re
from collections.abc import Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends

from ..conversations.api import Actor, ActorDependency
from ..sessionfiles.lookup import ConversationLookup, owned_conversation
from .citation_store import CitationStore
from .citations import CitationEntry
from .links import LinkSigner

EXCERPT_LIMIT = 600

# 知识源正文里内嵌的标签（KnowFlow 图片、表格块的 <img>/<table>，是它库内的表示）不是给人读的：
# 白名单认得出的标签整个剥掉、换成空格（相邻文字不粘连）——alt 这类属性里带中文的照剥（实测
# RAGFlow 的 <img> 几乎总带中文图注，不能拿「标签里有中文」当不算数的理由）；正文里偶发的
# 「x<y 且 z>w」这类比较符号，y 不在白名单里，自然原样保留。只收图片、表格这类实际出现的名字，
# 不收 b/i/p 这类单字母标签（正文里「x<b>5」会撞）。
TAG = re.compile(
    r"</?(?:img|table|thead|tbody|tfoot|caption|tr|td|th|br|hr|sup|sub)\b[^<>]*>",
    re.IGNORECASE,
)

# 正文里内嵌的 <img>（KnowFlow 图片块）解析成图给气泡显示：src 是相对地址（/minio/…）时按 open_url
# 的站点补全成绝对地址——KnowFlow 的 minio 匿名可读（实测 200），浏览器从 KnowWork 页面直接取得到
IMG_TAG = re.compile(r"<img\b[^<>]*>", re.IGNORECASE)
IMG_SRC = re.compile(r"""\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s">]+))""")
IMG_ALT = re.compile(r"""\balt\s*=\s*(?:"([^"]*)"|'([^']*)')""")
MAX_FIGURES = 3


def _figures(text: str, open_url: str | None) -> list[dict[str, str]]:
    """图最多取 3 张（气泡放不下更多）；补不出绝对地址的丢弃，alt 当图注。"""
    site = urlsplit(open_url) if open_url else None
    origin = f"{site.scheme}://{site.netloc}" if site and site.scheme and site.netloc else ""
    figures: list[dict[str, str]] = []
    for tag in IMG_TAG.findall(text):
        src = IMG_SRC.search(tag)
        value = next((group for group in src.groups() if group), "") if src else ""
        if value.startswith("/"):
            value = f"{origin}{value}" if origin else ""
        if not value.startswith(("http://", "https://")):
            continue
        alt = IMG_ALT.search(tag)
        figures.append({"url": value, "caption": next((group for group in alt.groups() if group), "") if alt else ""})
        if len(figures) >= MAX_FIGURES:
            break
    return figures


def _excerpt(text: str) -> str:
    clean = " ".join(TAG.sub(" ", text).split())
    return clean if len(clean) <= EXCERPT_LIMIT else clean[:EXCERPT_LIMIT] + "…"


def citation_view(entry: CitationEntry, sign: Callable[[str], str] | None = None) -> dict:
    """sign：给站内取图地址加签名（<img> 发不了自定义请求头，靠签名认人）。不给就不带图片。"""
    evidence = entry.evidence
    # 知识源给的版本提示（例如「可能已有新版《…》」）：给了才带，引用卡片上照原样显示，用户点开就知道该核对哪份
    note = {"versionNote": evidence.version_note} if evidence.version_note else {}
    return {
        "number": entry.number,
        "title": evidence.title,
        "excerpt": _excerpt(evidence.text),
        "figures": _figures(evidence.text, evidence.open_url),
        "page": evidence.locator.page if evidence.locator else None,
        "openUrl": evidence.open_url,
        "sourceId": evidence.source_id,
        "docRef": evidence.doc_ref,
        "locator": evidence.locator.model_dump(mode="json", exclude_defaults=True) if evidence.locator else None,
        # 这一块里每处的位置与原文：前端点开时整块高亮，回答里原样写了某处原文时只标那几处
        "locators": [loc.model_dump(mode="json", exclude_defaults=True) for loc in evidence.locators],
        "images": [{"url": sign(image.url), "caption": image.caption}
                   for image in evidence.images if image.url] if sign else [],
        **note,
    }


def build_router(citations: CitationStore, conversations: ConversationLookup,
                 current_actor: ActorDependency, links: LinkSigner | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/conversations")

    @router.get("/{conversation_id}/citations")
    async def list_citations(conversation_id: str, who: Actor = Depends(current_actor)):  # noqa: B008
        """回答里引用的原文片段"""
        conversation = await owned_conversation(conversations, conversation_id, who)
        catalog = await citations.load(conversation.workspace_id, conversation.id)
        sign = (lambda url: links.sign(url, who.user_id)) if links else None
        return {"items": [citation_view(entry, sign) for entry in catalog.entries],
                "truncated": catalog.truncated, "dropped": catalog.dropped}

    return router
