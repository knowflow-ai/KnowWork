"""知识源接口与证据模型。全部不可变。

`knowledge_search`、`knowledge_read` 两个工具只依赖这里的 `KnowledgeSource` 协议，
不知道背后是 KnowFlow 还是其他知识源。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_READ_CHARS = 5000
MAX_QUOTE = 300
MAX_META_FIELDS = 30  # 一条证据最多带这么多个元数据字段
MAX_LOCATORS = 200  # 一条证据最多带这么多个版面块的位置（补足、合并过的长片段，说明书一页几十个块）
MAX_PARTS = 100  # 一条合并过的证据最多由这么多块组成
MAX_CONTEXT = 5

LocatorType = Literal["pdf", "docx", "slide", "sheet", "text"]
# 检索方式：hybrid 语义加关键词（默认）；keyword 只按字面匹配，编号、报错码、型号这类要原样命中的用它
SearchMode = Literal["hybrid", "semantic", "keyword"]
# 能按次打开的检索方式：multi_hop 多跳（把几份文档里的内容串起来）、visual 视觉（按页面的样子找图表、版式、扫描件）。
# 建了索引不等于每次都用：知识库自己定默认（关闭、按需、开启），每次检索可以覆盖（关闭除外），见 SearchRequest。
# 不算可选能力（FEATURES）：它们只多加召回、不收窄范围，不认的知识源照常检索就行，不用另外说明
Route = Literal["multi_hop", "visual"]
FilterOp = Literal["eq", "ne", "in", "gt", "gte", "lt", "lte", "contains"]
# 知识源可以声明支持的可选能力（KnowledgeSource.features）。不支持的参数由业务服务去掉并在结果里说明，不静默
# pins：检索范围里按知识库点名文档与标签（用户 @ 的）；document_query：列文档时按名字找（@ 菜单搜文件）；
# fetch：取文件（原文件、单张图、页图），knowledge_fetch 放进会话工作区给智能体用
FEATURES = frozenset({"mode", "folders", "tags", "filters", "section", "chunk", "find", "pins", "document_query",
                      "fetch", "cited"})
MAX_FETCH_PAGES = 5  # 一次最多取几页页图：页图大，取多了放进会话也看不过来
MAX_PINNED = 50


@dataclass(frozen=True)
class Caller:
    """调用者身份，交给知识源实现，由它按这个身份过滤（第 5.4 节）。来自已校验的令牌。"""

    workspace_id: str
    user_id: str
    email: str | None = None


@dataclass(frozen=True)
class CitedDocument:
    """一轮回答引用到的一份文档（knowledge/cited.py 回合结束时交给声明了 cited 的知识源）。"""

    doc_ref: str
    title: str


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Box(_Frozen):
    """原文上的一个框。坐标是占页面宽高的比例（0–1，左上角为原点），和知识源自己的版面单位无关：
    各知识源存进来时自行换算，前端乘页面实际宽高画框。拿不到页面尺寸的来源不填框，只填页码。"""

    page: int = Field(ge=1)
    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("框的右下角不能在左上角的左边或上边")
        return self


# 每种定位只能带自己的字段：带了别的说明来源填错了，直接拒绝，免得前端拿到看不懂的组合
_TYPE_FIELDS: dict[str, frozenset[str]] = {
    "pdf": frozenset({"page", "boxes"}),
    "docx": frozenset({"block", "start", "end"}),
    "slide": frozenset({"page"}),
    "sheet": frozenset({"sheet", "row_start", "row_end"}),
    "text": frozenset({"start", "end"}),
}
_SPECIFIC_FIELDS = frozenset().union(*_TYPE_FIELDS.values())


class Locator(_Frozen):
    """引用在原文里的位置，type 决定用哪几个字段：

    - pdf：page（从 1 起）与 boxes；
    - docx：block 是解析器给的块序号；start / end 是这块对应 Word 正文的第几个到第几个段落或表格（左闭右开，从 0 起，
      数法照前端渲染 Word 用的 docx-preview：段落、表格、altChunk 各算一个，内容控件按里面的算）。Word 原件解析
      拿不到坐标，预览在浏览器里直接渲染原件，按 start / end 整段高亮；没有 start / end 时按 quote 找；
    - slide：page 当幻灯片序号用（从 1 起），同样靠 quote 高亮；
    - sheet：sheet 是工作表名，row_start / row_end 是 Excel 里的真实行号（从 1 起，含表头）；
    - text：start / end，纯文本里的字符区间（左闭右开）。
    quote 是被引用的原文（最多 300 字），source_hash 是原文件的版本哈希：标明这些坐标量的是哪一版原件。
    """

    type: LocatorType
    page: int | None = Field(default=None, ge=1)
    boxes: tuple[Box, ...] = ()
    block: int | None = Field(default=None, ge=0)
    sheet: str | None = Field(default=None, min_length=1, max_length=200)
    row_start: int | None = Field(default=None, ge=1)
    row_end: int | None = Field(default=None, ge=1)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, ge=0)
    quote: str = Field(default="", max_length=MAX_QUOTE)
    source_hash: str = Field(default="", max_length=128)
    section_path: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _fields_match_type(self) -> Self:
        stray = sorted(name for name in _SPECIFIC_FIELDS - _TYPE_FIELDS[self.type]
                       if getattr(self, name) not in (None, ()))
        if stray:
            raise ValueError(f"{self.type} 定位不能带 {', '.join(stray)}")
        if self.type == "slide" and self.page is None:
            raise ValueError("slide 定位要有幻灯片序号 page")
        if self.type == "sheet" and self.sheet is None:
            raise ValueError("sheet 定位要有工作表名 sheet")
        if None not in (self.row_start, self.row_end) and self.row_end < self.row_start:
            raise ValueError("row_end 不能小于 row_start")
        if None not in (self.start, self.end) and self.end < self.start:
            raise ValueError("end 不能小于 start")
        return self


def clip_quote(text: str) -> str:
    """把片段原文截成定位用的 quote：空白压成一个空格（前端在文字层查找时同样处理），最多 300 字。"""
    return " ".join(text.split())[:MAX_QUOTE]


class EvidenceImage(_Frozen):
    """片段里带的图片：ref 在知识源内稳定，caption 是图注与描述，url 是取图地址（经业务服务，不是存储直链）；
    at 是图在片段正文里的位置（字符偏移），知识源给了时检索工具把图标记插回正文原位，模型复述原文时图跟着原位走。
    context 是图注、描述都没有时的兜底：图所在章节与图前后的原文，只帮模型判断这张图配哪段话，不是图的内容。"""

    ref: str = Field(min_length=1, max_length=300)
    caption: str = ""
    url: str | None = None
    at: int | None = Field(default=None, ge=0)
    context: str = ""


class MetaItem(_Frozen):
    """文档的一个元数据字段：名字和给人看的值（多值用「、」连，数字、日期写成文本）。"""

    name: str = Field(min_length=1, max_length=64)
    value: str = Field(max_length=300)


class Evidence(_Frozen):
    source_id: str            # 知识源
    # 知识库（知识源内的 id），拼成 <source_id>.<kb_id> 就是授权与引用用的全局 id。
    # 引用目录里存的证据不带这一项（按文档引用就能找回），所以允许为空。
    kb_id: str = ""
    doc_ref: str              # 文档的稳定引用
    span_ref: str             # 片段的稳定引用
    title: str
    text: str
    locator: Locator | None = None
    # 片段里每个版面块的位置与原文（按正文先后，可选）。前端点开引用时整块高亮；回答里原样出现了某处原文时只标那几处
    locators: tuple[Locator, ...] = Field(default=(), max_length=MAX_LOCATORS)
    # 这一条由哪几块拼成（按正文先后，可选）：知识源把相邻命中合并、把短块补足成一段时给。排序、条数按整条算；
    # 交给模型、登记引用前拆回一块一块（expand_parts），每块单独编号、带自己的定位，模型引用哪块点开就是哪块。
    # 组成块自己不再带 parts
    parts: tuple["Evidence", ...] = Field(default=(), max_length=MAX_PARTS)
    open_url: str | None = None
    score: float | None = None  # 知识源自己的分数，只用于同源排序，不跨源比较
    images: tuple[EvidenceImage, ...] = ()
    # 版本提示（例如「这份文档已有更新版本」），知识源给出的一句话，原样展示给模型与用户
    version_note: str | None = Field(default=None, max_length=300)
    # 文档的日期（YYYY-MM-DD，可选，知识源给的上传或更新日期）：几份文件说法不一致时，模型据此判断哪份较新
    document_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # 文档的元数据（可选）：模型回答「哪年签的」「哪个部门的」这类问题直接用，也核对得了筛选条件生效没有
    metadata: tuple[MetaItem, ...] = Field(default=(), max_length=MAX_META_FIELDS)

    @property
    def key(self) -> tuple[str, str]:
        return (self.source_id, self.span_ref)


def expand_parts(evidence: Sequence[Evidence]) -> tuple[Evidence, ...]:
    """合并过的证据拆成组成它的一块一块（同一块只留第一次出现的），没合并过的原样。"""
    seen: set[tuple[str, str]] = set()
    result: list[Evidence] = []
    for item in evidence:
        for part in item.parts or (item,):
            if part.key not in seen:
                seen.add(part.key)
                result.append(part.model_copy(update={"parts": ()}) if part.parts else part)
    return tuple(result)


class Pin(_Frozen):
    """用户这一轮在一个知识库里点名的范围（@ 文件、@ 标签）：这几份文档，加上带这几个标签的文档。"""

    dataset_id: str
    document_ids: tuple[str, ...] = Field(default=(), max_length=MAX_PINNED)  # 文档引用 doc_ref
    tags: tuple[str, ...] = Field(default=(), max_length=MAX_PINNED)


class Scope(_Frozen):
    """检索范围：本次要查的知识库（知识源内的 id）。业务服务已把它与调用者的授权、知识源的开放范围取过交集，
    不会为空（第 1 层收窄）；知识源再按自己的权限收窄（第 2 层）。

    pins 里的知识库只查点名的文档与标签，其余知识库整库查（用户 @ 了整个库又 @ 了库里的文件时，整库优先，
    业务服务已去掉这样的点名）。document_ids 是不支持 pins 的知识源的退路：所有知识库都只点名了文档时，
    业务服务把它们合起来放这里。"""

    dataset_ids: tuple[str, ...] = Field(min_length=1)
    document_ids: tuple[str, ...] = ()
    pins: tuple[Pin, ...] = ()


FieldType = Literal["text", "number", "date"]


class FieldInfo(_Frozen):
    """知识库的一个元数据字段，给 Agent 写过滤条件用。multi 的字段能填多个值；标签是名为「标签」的多值字段。"""

    name: str = Field(min_length=1, max_length=64)
    type: FieldType = "text"
    multi: bool = False
    options: tuple[str, ...] = Field(default=(), max_length=200)
    description: str = Field(default="", max_length=500)


class BaseInfo(_Frozen):
    """知识源里的一个知识库。id 是知识源内的 id；description、fields 知识源有就给，没有留空。"""

    id: str
    name: str
    description: str = ""
    fields: tuple[FieldInfo, ...] = ()
    # 所在目录，从顶层往下的名字（知识源有目录时给）：@ 菜单里写在库名后面，也能整个目录一起选
    path: tuple[str, ...] = ()
    # 这个库能按次打开的检索方式（建了索引、检索时不是「关闭」）：目录里标给模型，告诉它哪些库开 multi_hop、visual 有用
    routes: tuple[Route, ...] = ()


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


class MetaFilter(_Frozen):
    """一个元数据条件：字段 运算 值。值一律是字符串，数字、日期按知识源里该字段的类型比较；in 用 values。"""

    field: str = Field(min_length=1, max_length=64)
    op: FilterOp = "eq"
    value: str | None = Field(default=None, max_length=200)
    values: tuple[str, ...] = Field(default=(), max_length=50)

    @model_validator(mode="after")
    def _value_matches_op(self) -> Self:
        if self.op == "in" and (not self.values or self.value is not None):
            raise ValueError("in 条件用 values 给出候选值")
        if self.op != "in" and (self.value is None or self.values):
            raise ValueError(f"{self.op} 条件用 value 给出一个值")
        return self


class SearchRequest(_Frozen):
    """一次检索。query、scope、top_n 每个知识源都支持；其余是可选能力，知识源在 features 里声明。"""

    query: str = Field(min_length=1, max_length=1000)
    scope: Scope
    top_n: int = Field(default=8, ge=1, le=100)
    mode: SearchMode = "hybrid"
    folders: tuple[str, ...] = Field(default=(), max_length=20)  # 知识库内的文件夹（相对路径，含子文件夹）
    tags: tuple[str, ...] = Field(default=(), max_length=20)     # 文档带其中任何一个标签就算（按主题限定范围）
    filters: tuple[MetaFilter, ...] = Field(default=(), max_length=10)
    # 这一次开不开多跳、视觉检索：None 按知识库自己的默认（按需的不用、开启的用）；True、False 覆盖默认，
    # 但覆盖不了知识库设成「关闭」的
    multi_hop: bool | None = None
    visual: bool | None = None

    def wants(self) -> frozenset[str]:
        """这次用到的可选能力（点名范围不在这里：不支持时业务服务另行处理，见 Scope）。"""
        used = {"mode"} if self.mode != "hybrid" else set()
        used |= {name for name in ("folders", "tags", "filters") if getattr(self, name)}
        return frozenset(used)


class OutlineNode(_Frozen):
    """文档目录的一节。number 按目录树的位置编（2、2.1），阅读时 section 给它就读这一节（含子节）；页码、字数、
    图表数都算上子节，模型据此挑要读哪一节、读多长。没有页码的文档（Word）页码为空。"""

    number: str
    title: str
    level: int
    page_start: int | None = None
    page_end: int | None = None
    chars: int = 0
    figures: int = 0
    tables: int = 0


class FetchRequest(_Frozen):
    """取文件（knowledge_fetch）：what 是 file 原文件、image 单张图（image_ref 是检索结果里那张图的 ref）、
    pages 页图（first_page 到 last_page，从 1 起，最多 MAX_FETCH_PAGES 页）。"""

    doc_ref: str = Field(min_length=1, max_length=500)
    what: Literal["file", "image", "pages"]
    image_ref: str | None = Field(default=None, min_length=1, max_length=300)
    first_page: int | None = Field(default=None, ge=1)
    last_page: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _complete(self) -> Self:
        if self.what == "image" and not self.image_ref:
            raise ValueError("取单张图要给 image_ref")
        if self.what == "pages":
            if self.first_page is None:
                raise ValueError("取页图要给页码")
            last = self.last_page or self.first_page
            if last < self.first_page or last - self.first_page + 1 > MAX_FETCH_PAGES:
                raise ValueError(f"页码范围不对：一次最多取 {MAX_FETCH_PAGES} 页")
        return self


class FetchedFile(_Frozen):
    """取到的一个文件：name 是给人看的文件名（含扩展名），page 是页图对应的页码。"""

    name: str = Field(min_length=1, max_length=200)
    mime: str = Field(min_length=1, max_length=200)
    data: bytes
    page: int | None = None


class ReadRequest(_Frozen):
    """一次阅读。默认从 cursor（或开头）顺序读；section、chunk、find 三选一；outline 是看目录，可以配 section 只看那一章
    下面的子目录。知识源在 features 里声明支持哪些。"""

    doc_ref: str = Field(min_length=1, max_length=300)
    cursor: Cursor | None = None
    max_chars: int = Field(default=MAX_READ_CHARS, ge=1, le=MAX_READ_CHARS)
    section: str | None = Field(default=None, min_length=1, max_length=300)  # 按节读：章节标题或「一级/二级」路径
    chunk: str | None = Field(default=None, min_length=1, max_length=300)    # 从这个片段（span_ref）读起
    context: int = Field(default=1, ge=0, le=MAX_CONTEXT)                    # chunk 前后各带几个片段
    find: str | None = Field(default=None, min_length=1, max_length=200)     # 文档内查找：词或正则
    outline: bool = False                                                    # 看目录（配 section 只看那一章）

    @model_validator(mode="after")
    def _one_way(self) -> Self:
        if len([name for name in ("section", "chunk", "find") if getattr(self, name)]) > 1:
            raise ValueError("section、chunk、find 只能选一个")
        if self.outline and (self.chunk or self.find):
            raise ValueError("看目录（outline）只能配 section，不能和 chunk、find 一起用")
        return self

    def wants(self) -> frozenset[str]:
        return frozenset(name for name in ("outline", "section", "chunk", "find") if getattr(self, name))


class SearchTraceEntry(_Frozen):
    """检索过程里的一项（只记录、给运行详情看，不交给模型）：哪一路或哪一步、一句结论、几行明细。
    例如「视觉检索」「命中 6 页、展开成 9 块」「手册 第 3 页 分数 12.41，进了结果第 1 名」。"""

    label: str = Field(max_length=50)
    summary: str = Field(default="", max_length=500)
    lines: tuple[str, ...] = Field(default=(), max_length=50)
    source: str = Field(default="", max_length=100)  # 哪个知识源；知识服务合并时填上


class SearchHits(_Frozen):
    """知识源的检索结果连同给模型的说明（例如按同义词表补了哪些词，模型才知道结果里的另一个说法就是它问的）。
    trace 是检索过程（各路召回、重排的情况），只进检索记录与运行详情，不给模型：给模型会费上下文还会干扰作答。
    知识源也可以只返回证据列表。"""

    evidence: tuple[Evidence, ...]
    notes: tuple[str, ...] = ()
    trace: tuple[SearchTraceEntry, ...] = ()


class ReadResult(_Frozen):
    doc_ref: str
    title: str
    text: str
    spans: tuple[Evidence, ...] = ()   # 本次读到的片段，供注册进引用目录
    next_cursor: Cursor | None = None  # None 表示已读完
    notes: tuple[str, ...] = ()        # 给模型的说明，例如某个参数这个知识源不支持、已按顺序阅读
    outline: tuple[OutlineNode, ...] = ()  # 看目录时的各节（按阅读顺序）；不是看目录或这份文档没有可靠目录时为空


class Page(_Frozen):
    token: str | None = None
    size: int = Field(default=30, ge=1, le=100)
    # 按名字找（包含这几个字，不分大小写）；空串表示按最近更新列（@ 菜单刚进「文件」组）。知识源声明了 document_query 才会给
    query: str | None = Field(default=None, max_length=100)


class DocumentInfo(_Frozen):
    """列文档时的一份。file_name、updated_at、outline 是知识源给得出来才有的：outline 为真表示 knowledge_read 能给目录。"""

    doc_ref: str
    title: str
    open_url: str | None = None
    file_name: str | None = None
    updated_at: datetime | None = None
    outline: bool | None = None


class DocumentPage(_Frozen):
    items: tuple[DocumentInfo, ...]
    next_token: str | None = None


class KnowledgeSource(Protocol):
    source_id: str
    features: frozenset[str]  # 支持 FEATURES 里的哪些可选能力

    async def list_bases(self, caller: Caller) -> tuple[BaseInfo, ...]:
        """开放范围内、调用者在知识源侧读得到的知识库（统一账号方式下就是那个账号读得到的）。"""
        ...

    def base_of(self, doc_ref: str) -> str:
        """文档引用属于哪个知识库（知识源内的 id）；格式不对抛 InvalidInput。"""
        ...

    async def search(self, caller: Caller, request: SearchRequest) -> "list[Evidence] | SearchHits": ...

    async def read(self, caller: Caller, request: ReadRequest) -> ReadResult: ...

    async def list_documents(self, caller: Caller, scope: Scope, page: Page) -> DocumentPage: ...

    async def probe(self, caller: Caller) -> None:
        """连通测试：以调用者身份发一个最小请求，失败抛 KnowledgeError。"""
        ...

    async def fetch(self, caller: Caller, request: FetchRequest) -> tuple[FetchedFile, ...]:
        """取文件（features 里声明了 fetch 才会被调用）：原文件、单张图或几页页图。取不到抛 KnowledgeError。"""
        ...

    async def cited(self, caller: Caller, documents: tuple[CitedDocument, ...]) -> None:
        """回合结束时告诉知识源：调用者这一轮的回答引用了它的这几份文档（features 里声明了 cited 才会被调用，
        只给扩展提供的知识源）。例如按人记常用资料，检索时给常用的加一点分。"""
        ...
