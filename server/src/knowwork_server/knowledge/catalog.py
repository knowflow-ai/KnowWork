"""每轮的知识库目录：照 DSH 官方技能目录的形态，给模型名字、截短的描述和能筛的字段
（只有名字和类型）；可选值、标签集由模型按需调 knowledge_bases 取（不用过滤的回合不多花 token）。
原来只给名字和描述，要多调一次 knowledge_bases 才知道有哪些字段，模型多数时候直接搜、不筛。宿主负责渲染、按内容去重。"""

from ..kernel.contract import KnowledgeCatalog, KnowledgeEntry
from .models import FieldInfo
from .service import BaseListing

MAX_ENTRIES = 100
MAX_DESCRIPTION = 200
MAX_FIELDS = 30
TYPE_NAMES = {"text": "文本", "number": "数字", "date": "日期"}


def _short(text: str) -> str:
    clean = " ".join((text or "").split())
    return clean if len(clean) <= MAX_DESCRIPTION else f"{clean[:MAX_DESCRIPTION - 1]}…"


def _field(field: FieldInfo) -> str:
    kind = TYPE_NAMES.get(field.type, field.type)
    return f"{field.name}（{'多值' if field.multi else ''}{kind}）"


def turn_catalog(listing: BaseListing) -> KnowledgeCatalog:
    entries = tuple(KnowledgeEntry(id=b.id, name=b.info.name[:200] or b.id, description=_short(b.info.description),
                                   fields=tuple(_field(f) for f in b.info.fields[:MAX_FIELDS]) if b.filterable else ())
                    for b in listing.bases[:MAX_ENTRIES])
    return KnowledgeCatalog(bases=entries, omitted=max(0, len(listing.bases) - MAX_ENTRIES))
