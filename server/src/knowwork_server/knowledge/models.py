"""知识源接口与证据模型（技术方案 8.1 节）。全部不可变。

`knowledge_search`、`knowledge_read` 两个工具只依赖这里的 `KnowledgeSource` 协议，
不知道背后是 KnowFlow 还是其他知识源。
"""

from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

MAX_READ_CHARS = 5000


@dataclass(frozen=True)
class Caller:
    """调用者身份，交给知识源实现，由它按这个身份过滤（第 5.4 节）。来自已校验的令牌。"""

    workspace_id: str
    user_id: str
    email: str | None = None


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Box(_Frozen):
    """原文坐标框：页码从 1 开始，坐标沿用知识源的版面坐标。"""

    page: int = Field(ge=1)
    x0: float
    x1: float
    top: float
    bottom: float


class Locator(_Frozen):
    page: int | None = Field(default=None, ge=1)
    boxes: tuple[Box, ...] = ()
    section_path: tuple[str, ...] = ()


class Evidence(_Frozen):
    source_id: str            # 知识源
    # 知识库（知识源内的 id），拼成 <source_id>.<kb_id> 就是授权与引用用的全局 id（技术方案第 8 章开头）。
    # 引用目录里存的证据不带这一项（按文档引用就能找回），所以允许为空。
    kb_id: str = ""
    doc_ref: str              # 文档的稳定引用
    span_ref: str             # 片段的稳定引用
    title: str
    text: str
    locator: Locator | None = None
    open_url: str | None = None
    score: float | None = None  # 知识源自己的分数，只用于同源排序，不跨源比较

    @property
    def key(self) -> tuple[str, str]:
        return (self.source_id, self.span_ref)


class Scope(_Frozen):
    """检索范围：本次要查的知识库（知识源内的 id）。业务服务已把它与调用者的授权、知识源的开放范围取过交集，
    不会为空（技术方案 5.4 节第 1 层）；知识源再按自己的权限收窄（第 2 层）。"""

    dataset_ids: tuple[str, ...] = Field(min_length=1)
    document_ids: tuple[str, ...] = ()


class BaseInfo(_Frozen):
    """知识源里的一个知识库。id 是知识源内的 id。"""

    id: str
    name: str


class Cursor(_Frozen):
    """续读位置：分片页、页内序号、片段内字符偏移。对模型是不透明字符串。"""

    page: int = Field(default=1, ge=1)
    index: int = Field(default=0, ge=0)
    offset: int = Field(default=0, ge=0)

    def encode(self) -> str:
        return f"{self.page}.{self.index}.{self.offset}"

    @staticmethod
    def decode(text: str) -> "Cursor":
        try:
            page, index, offset = (int(part) for part in text.split("."))
            return Cursor(page=page, index=index, offset=offset)
        except (ValueError, TypeError) as error:
            raise ValueError("续读游标格式错误") from error


class ReadResult(_Frozen):
    doc_ref: str
    title: str
    text: str
    spans: tuple[Evidence, ...] = ()   # 本次读到的片段，供注册进引用目录
    next_cursor: Cursor | None = None  # None 表示已读完


class Page(_Frozen):
    token: str | None = None
    size: int = Field(default=30, ge=1, le=100)


class DocumentInfo(_Frozen):
    doc_ref: str
    title: str
    open_url: str | None = None


class DocumentPage(_Frozen):
    items: tuple[DocumentInfo, ...]
    next_token: str | None = None


class KnowledgeSource(Protocol):
    source_id: str

    async def list_bases(self, caller: Caller) -> tuple[BaseInfo, ...]:
        """开放范围内、调用者在知识源侧读得到的知识库（统一账号方式下就是那个账号读得到的）。"""
        ...

    def base_of(self, doc_ref: str) -> str:
        """文档引用属于哪个知识库（知识源内的 id）；格式不对抛 InvalidInput。"""
        ...

    async def search(self, caller: Caller, query: str, scope: Scope, top_n: int) -> list[Evidence]: ...

    async def read(self, caller: Caller, doc_ref: str, cursor: Cursor | None, max_chars: int) -> ReadResult: ...

    async def list_documents(self, caller: Caller, scope: Scope, page: Page) -> DocumentPage: ...

    async def probe(self, caller: Caller) -> None:
        """连通测试：以调用者身份发一个最小请求，失败抛 KnowledgeError。"""
        ...
