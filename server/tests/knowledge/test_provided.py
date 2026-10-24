"""扩展提供的知识源：不在知识源表里，开放范围是它自己的全部知识库，谁能查只看知识库授权；
和配置的知识源一起检索、按名次合并；运行配置的可查列表也算上它。"""

import pytest

from knowwork_server.knowledge import ProvidedSource
from knowwork_server.knowledge.errors import NoReadableKnowledge, NotFound
from knowwork_server.knowledge.models import BaseInfo, Evidence, Locator, ReadResult, SearchHits

pytestmark = pytest.mark.anyio


class Builtin:
    source_id = "builtin"
    features = frozenset({"mode"})

    def __init__(self):
        self.requests = []
        self.hits: list[Evidence] | None = None  # 给了就原样返回

    async def list_bases(self, caller):
        return (BaseInfo(id="kb1", name="员工手册"), BaseInfo(id="kb2", name="机密库"))

    def base_of(self, doc_ref):
        return doc_ref.split("/", 1)[0]

    async def search(self, caller, request):
        self.requests.append(request)
        if self.hits is not None:
            return self.hits
        return [Evidence(source_id="builtin", kb_id=kb, doc_ref=f"{kb}/d1", span_ref=f"{kb}-s1", title="手册",
                         text="请假要提前三天") for kb in request.scope.dataset_ids]

    async def read(self, caller, request):
        return ReadResult(doc_ref=request.doc_ref, title="手册", text="全文")

    async def list_documents(self, caller, scope, page):
        raise NotImplementedError

    async def probe(self, caller):
        return None


@pytest.fixture
def builtin(env):
    source = Builtin()
    env.knowledge.provided.add(ProvidedSource(id="builtin", name="扩展知识库", kind="builtin", source=source))
    return source


async def caller(env, user):
    return await env.knowledge.service.caller("ws1", user)


async def test_only_granted_bases_are_searched(env, builtin):
    with pytest.raises(NoReadableKnowledge):
        await env.knowledge.service.search(await caller(env, "carol"), "请假")
    await env.access.grant("alice", "ws1", "user:carol", "knowledge_base:builtin.kb1", "use")
    outcome = await env.knowledge.service.search(await caller(env, "carol"), "请假")
    assert [e.kb_id for e in outcome.evidence] == ["kb1"], "没授权的 kb2 不发给知识源"
    assert builtin.requests[-1].scope.dataset_ids == ("kb1",) and builtin.requests[-1].mode == "hybrid"


def _slide(page: int, span_ref: str | None = None) -> Evidence:
    return Evidence(source_id="builtin", kb_id="kb1", doc_ref="kb1/d1", span_ref=span_ref or f"s{page}", title="产品说明",
                    text=f"第{page}页的话", locator=Locator(type="slide", page=page, quote=f"第{page}页的话"))


async def test_merged_results_count_as_one_and_are_cited_piece_by_piece(env, builtin):
    """照 WeKnora（expandCitationSources）：知识源把相邻命中合并、补足成一段，排序与条数按整段算；交给模型、登记引用前
    拆回原来的一块一块，每块一条、带自己的定位，模型引用哪块点开就是哪块。原来整段一个编号，一份 PPT 的 19 页
    合成一条，点开跳到了不相干的页。"""
    whole = _slide(2).model_copy(update={"text": "第1页的话 第2页的话 第3页的话",
                                         "parts": (_slide(1), _slide(2), _slide(3))})
    builtin.hits = [whole, _slide(9), _slide(20)]
    await env.access.grant("alice", "ws1", "user:carol", "knowledge_base:builtin.kb1", "use")
    outcome = await env.knowledge.service.search(await caller(env, "carol"), "部署", top_n=2)
    assert [e.span_ref for e in outcome.evidence] == ["s1", "s2", "s3", "s9"], "截断按整段算，之后按块展开"
    assert [e.locator.page for e in outcome.evidence] == [1, 2, 3, 9]
    assert all(not e.parts for e in outcome.evidence)


async def test_whole_source_grants_list_bases_as_the_caller(env, builtin):
    await env.access.grant("alice", "ws1", "user:carol", "knowledge_base:builtin.*", "use")
    outcome = await env.knowledge.service.search(await caller(env, "carol"), "请假")
    assert {e.kb_id for e in outcome.evidence} == {"kb1", "kb2"}


async def test_queryable_list_includes_it_for_the_runtime_profile(env, builtin):
    await env.access.grant("alice", "ws1", "user:carol", "knowledge_base:builtin.kb1", "use")
    [entry] = await env.knowledge.admin.queryable("carol", "ws1")
    assert (entry.id, entry.name, entry.kind, entry.bases) == ("builtin", "扩展知识库", "builtin", ("kb1",))
    assert await env.knowledge.admin.queryable("dave", "ws1") == ()
    assert all(s.id != "builtin" for s in await env.knowledge.admin.list("alice", "ws1")), "管理页里看不到它"


async def test_grants_page_lists_its_bases_for_those_who_can_grant(env, builtin):
    """授权页的知识库下拉也要有它：原来只列知识源表里配置的连接，扩展的知识库一个都选不到，没法给它的知识库授权（实测）。
    只有能授权的人（空间管理员）看得到。"""
    [entry] = [c for c in await env.knowledge.admin.catalog("bob", "ws1") if c.source_id == "builtin"]
    assert entry.source_name == "扩展知识库" and entry.enabled and entry.error is None
    assert [(b.id, b.kb_id, b.name) for b in entry.bases] == [("builtin.kb1", "kb1", "员工手册"),
                                                             ("builtin.kb2", "kb2", "机密库")]
    assert await env.knowledge.admin.catalog("carol", "ws1") == (), "编辑角色不能授权，也看不到"


async def test_reading_needs_a_grant_on_the_documents_base(env, builtin):
    await env.access.grant("alice", "ws1", "user:carol", "knowledge_base:builtin.kb1", "use")
    result = await env.knowledge.service.read(await caller(env, "carol"), "builtin", "kb1/d1")
    assert result.text == "全文"
    with pytest.raises(NotFound):
        await env.knowledge.service.read(await caller(env, "carol"), "builtin", "kb2/d1")
    with pytest.raises(NotFound):
        await env.knowledge.service.read(await caller(env, "dave"), "builtin", "kb1/d1")


async def test_a_source_can_send_notes_with_its_hits(env, builtin):
    """知识源可以把给模型的说明连同结果一起交回（例如按同义词表补了哪些词）：模型只看到「微短剧」的段落、
    不知道它就是问的「短AB」，会判断知识库里没有，换着词反复搜（实测）。只返回结果列表的知识源照旧。"""
    async def with_notes(caller, request):
        return SearchHits(evidence=tuple(await Builtin.search(builtin, caller, request)),
                          notes=("按知识库的同义词表，「短AB」和「微短剧」是同一个意思",))

    builtin.search = with_notes
    await env.access.grant("alice", "ws1", "user:carol", "knowledge_base:builtin.kb1", "use")
    outcome = await env.knowledge.service.search(await caller(env, "carol"), "短AB 是什么")
    assert [e.kb_id for e in outcome.evidence] == ["kb1"]
    assert outcome.notes == ("按知识库的同义词表，「短AB」和「微短剧」是同一个意思",)


async def test_ids_must_not_look_like_configured_sources(env):
    for bad in ("ks_abc", "Bad", "x"):
        with pytest.raises(ValueError):
            env.knowledge.provided.add(ProvidedSource(id=bad, name="x", kind="x", source=Builtin()))
