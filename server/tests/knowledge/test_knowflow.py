import httpx
import pytest

from knowwork_server.knowledge.assertion import AssertionSigner
from knowwork_server.knowledge.errors import IdentityUnavailable, InvalidInput, ScopeDenied, SourceUnavailable
from knowwork_server.knowledge.knowflow import (
    AssertionIdentity,
    KnowFlowSource,
    SharedAccountIdentity,
)
from knowwork_server.knowledge.knowflow_mapping import KnowFlowConfig, locator_of, open_url
from knowwork_server.knowledge.models import Caller, Cursor, Page, Scope

from .knowflow_stub import SERVICE_KEY, KnowFlowStub, StubUser, chunk

pytestmark = pytest.mark.anyio
ALICE = Caller("ws1", "u-alice", "alice@corp.com")
BOB = Caller("ws1", "u-bob", "bob@corp.com")
NOBODY = Caller("ws1", "u-x", "nobody@corp.com")
CAROL = Caller("ws1", "u-c", "carol@corp.com")
# 业务服务交进来的范围总是收窄过、非空的（技术方案 8.1 节）
BOTH = Scope(dataset_ids=("ds1", "ds2"))


def make_stub():
    return KnowFlowStub(
        users=[StubUser("kf-a", "alice@corp.com", {"ds1", "ds2"}), StubUser("kf-b", "bob@corp.com", {"ds1"}),
               StubUser("kf-c", "carol@corp.com", set()), StubUser("kf-d", "dave@corp.com", {"ds1"}, disabled=True)],
        api_keys={"ragflow-alicekey": "alice@corp.com", "ragflow-bobkey": "bob@corp.com",
                  "ragflow-carolkey": "carol@corp.com"},
        chunks={"ds1": [chunk("ds1", "contract", 1), chunk("ds1", "contract", 2)],
                "ds2": [chunk("ds2", "secret", 1, "机密条款")]},
        documents={"contract": [chunk("ds1", "contract", i, "甲" * 30) for i in range(1, 6)]},
    )


def source(stub, *, mode="assertion", datasets=("ds1", "ds2"), web=None, shared_key=None):
    config = KnowFlowConfig(auth_mode=mode, dataset_ids=datasets, web_base_url=web)
    identity = SharedAccountIdentity(shared_key) if mode == "shared" else AssertionIdentity(AssertionSigner(SERVICE_KEY))
    client = httpx.AsyncClient(transport=stub.transport())
    return KnowFlowSource(source_id="src1", name="合同库", base_url="http://kf.local/", config=config,
                          identity=identity, client=client)


async def test_two_users_with_different_permissions_see_different_evidence():
    stub = make_stub()
    kf = source(stub)
    alice = await kf.search(ALICE, "条款", BOTH, 10)
    bob = await kf.search(BOB, "条款", BOTH, 10)
    assert {e.doc_ref for e in alice} == {"ds1/contract", "ds2/secret"}
    assert {e.doc_ref for e in bob} == {"ds1/contract"}, "bob 读不了 ds2，证据里不能有它"
    first = alice[0]
    assert first.source_id == "src1" and first.kb_id == "ds1" and first.span_ref == "contract-c1"
    assert first.locator.page == 1 and first.locator.boxes[0].x1 == 200
    assert first.open_url == "http://kf.local/document/contract?ext=pdf&prefix=document&page=1"


async def test_every_request_carries_a_fresh_assertion_with_the_callers_email():
    stub = make_stub()
    await source(stub).search(BOB, "条款", BOTH, 5)  # 第一次 403，去掉 ds2 后重试
    assertions = [r.headers["x-knowflow-assertion"] for r in stub.requests]
    assert len(assertions) == 2 and assertions[0] != assertions[1]
    assert "authorization" not in stub.requests[0].headers


async def test_user_with_no_readable_dataset_is_refused_with_reason():
    stub = make_stub()
    with pytest.raises(ScopeDenied, match="都没有读取权限"):
        await source(stub).search(CAROL, "条款", BOTH, 5)
    assert await source(stub, datasets=()).list_bases(CAROL) == ()


async def test_list_bases_is_what_the_caller_reads_within_the_open_range():
    """开放范围留空：调用者在 KnowFlow 读得到的全部；配置了：只列范围内的。名字取 KnowFlow 的。"""
    stub = make_stub()
    assert [(b.id, b.name) for b in await source(stub, datasets=()).list_bases(BOB)] == [("ds1", "库ds1")]
    assert [b.id for b in await source(stub, datasets=()).list_bases(ALICE)] == ["ds1", "ds2"]
    assert [b.id for b in await source(stub, datasets=("ds2", "ds9")).list_bases(ALICE)] == ["ds2"]


async def test_base_of_reads_the_dataset_from_the_document_ref():
    kf = source(make_stub(), datasets=("ds1",))
    assert kf.base_of("ds1/contract") == "ds1"
    with pytest.raises(InvalidInput, match="开放范围"):
        kf.base_of("ds2/secret")
    with pytest.raises(InvalidInput):
        kf.base_of("no-slash")


async def test_shared_account_mode_sees_one_account_for_everyone():
    """统一账号：不论谁来查，KnowFlow 看到的都是配置的那个账号；收窄只靠业务服务交进来的范围。"""
    stub = make_stub()
    kf = source(stub, mode="shared", shared_key="ragflow-alicekey")
    assert {e.doc_ref for e in await kf.search(BOB, "q", BOTH, 5)} == {"ds1/contract", "ds2/secret"}
    assert {e.doc_ref for e in await kf.search(BOB, "q", Scope(dataset_ids=("ds1",)), 5)} == {"ds1/contract"}
    assert {r.headers["authorization"] for r in stub.requests} == {"Bearer ragflow-alicekey"}
    assert "x-knowflow-assertion" not in stub.requests[0].headers
    with pytest.raises(IdentityUnavailable, match="统一账号 Key 无效"):
        await source(stub, mode="shared", shared_key="ragflow-revoked").search(ALICE, "q", BOTH, 5)


async def test_shared_account_probe_checks_every_open_base_is_readable():
    stub = make_stub()
    await source(stub, mode="shared", shared_key="ragflow-alicekey").probe(CAROL)
    with pytest.raises(ScopeDenied, match="ds2"):
        await source(stub, mode="shared", shared_key="ragflow-bobkey").probe(ALICE)
    open_all = source(stub, mode="shared", shared_key="ragflow-bobkey", datasets=())
    await open_all.probe(ALICE)  # 范围留空：这个账号读得到的全部
    assert [b.id for b in await open_all.list_bases(ALICE)] == ["ds1"], "列的是账号读得到的，与调用者无关"
    with pytest.raises(ScopeDenied, match="一个知识库都读不到"):
        await source(stub, mode="shared", shared_key="ragflow-carolkey", datasets=()).probe(ALICE)


async def test_user_without_knowflow_account_or_disabled_is_refused():
    stub = make_stub()
    with pytest.raises(IdentityUnavailable, match="没有对应账号"):
        await source(stub).search(NOBODY, "x", BOTH, 5)
    with pytest.raises(IdentityUnavailable, match="已停用"):
        await source(stub).search(Caller("ws1", "u-d", "dave@corp.com"), "x", BOTH, 5)
    with pytest.raises(IdentityUnavailable, match="没有邮箱"):
        await source(stub).search(Caller("ws1", "u-e"), "x", BOTH, 5)


async def test_knowflow_without_assertion_support_is_an_identity_failure_not_an_empty_list():
    """不支持签名断言的 KnowFlow 忽略断言头，回 code 0、data false：要当身份失败报出来，不能当成没有知识库。"""
    stub = make_stub()
    stub.assertion_supported = False
    kf = source(stub, datasets=())
    for attempt in (kf.list_bases(ALICE), kf.probe(ALICE), kf.search(ALICE, "q", BOTH, 5)):
        with pytest.raises(IdentityUnavailable, match="没有接受签名断言"):
            await attempt


async def test_expired_or_replayed_assertion_is_not_accepted_by_knowflow():
    stub = make_stub()
    stub.now = 10**10  # KnowFlow 时钟远在断言过期之后
    with pytest.raises(SourceUnavailable):
        await source(stub).search(ALICE, "x", BOTH, 5)
    stub = make_stub()
    kf = source(stub)
    await kf.probe(ALICE)
    replay = httpx.AsyncClient(transport=stub.transport())
    response = await replay.get("http://kf.local/api/v1/datasets",
                                headers={"x-knowflow-assertion": stub.requests[0].headers["x-knowflow-assertion"]})
    assert response.status_code == 401 and response.json()["error_code"] == "assertion_replayed"


async def test_scope_outside_configuration_is_rejected_and_inside_is_narrowed():
    stub = make_stub()
    kf = source(stub)
    with pytest.raises(InvalidInput, match="开放范围"):
        await kf.search(ALICE, "q", Scope(dataset_ids=("ds9",)), 5)
    with pytest.raises(ValueError):
        Scope(dataset_ids=())
    hits = await kf.search(ALICE, "q", Scope(dataset_ids=("ds2",)), 5)
    assert {e.doc_ref for e in hits} == {"ds2/secret"}


async def test_read_pages_through_a_document_with_cursor():
    stub = make_stub()
    kf = source(stub)
    first = await kf.read(ALICE, "ds1/contract", None, 70)
    assert len(first.text) <= 70 and first.next_cursor is not None
    assert first.title == "contract.pdf" and first.spans[0].span_ref == "contract-c1"
    pieces, cursor = [first.text], first.next_cursor
    while cursor is not None:
        part = await kf.read(ALICE, "ds1/contract", Cursor.decode(cursor.encode()), 70)
        pieces.append(part.text)
        cursor = part.next_cursor
    assert "".join(pieces).count("甲") == 150, "续读不重不漏"


async def test_read_is_checked_against_scope_and_permissions():
    stub = make_stub()
    stub.documents["secret"] = [chunk("ds2", "secret", 1)]
    with pytest.raises(ScopeDenied):
        await source(stub).read(BOB, "ds2/secret", None, 100)
    with pytest.raises(InvalidInput):
        await source(stub, datasets=("ds1",)).read(ALICE, "ds2/secret", None, 100)
    with pytest.raises(InvalidInput):
        await source(stub).read(ALICE, "no-slash", None, 100)


async def test_list_documents_walks_datasets():
    stub = make_stub()
    kf = source(stub)
    page = await kf.list_documents(ALICE, BOTH, Page(size=1))
    assert [d.doc_ref for d in page.items] == ["ds1/contract"]
    page = await kf.list_documents(ALICE, BOTH, Page(token=page.next_token, size=1))
    assert [d.doc_ref for d in page.items] == ["ds2/secret"] and page.next_token is None
    with pytest.raises(InvalidInput):
        await kf.list_documents(ALICE, BOTH, Page(token="x"))
    assert (await kf.list_documents(ALICE, BOTH, Page(token="9.1"))).items == ()


async def test_transport_failure_is_reported_without_details():
    stub = make_stub()
    stub.down = True
    with pytest.raises(SourceUnavailable) as info:
        await source(stub).search(ALICE, "q", BOTH, 5)
    assert str(info.value) == "知识源「合同库」暂时不可用"


def test_mapping_helpers():
    assert locator_of("bad") is None
    assert locator_of([[2, 1, 2, 3, 4], ["x"], [None, 1, 2, 3, 4]]).page == 2
    assert open_url("http://w", "d 1", "a", None) == "http://w/document/d%201?ext=&prefix=document"
    with pytest.raises(ValueError):
        KnowFlowConfig(dataset_ids=("bad id",))
    with pytest.raises(ValueError):
        KnowFlowConfig(web_base_url="ftp://x")


def retrieval_requests(stub):
    return [r for r in stub.requests if r.url.path == "/api/v1/retrieval"]


async def test_datasets_with_different_embedding_models_are_searched_per_model_and_merged():
    """真实 KnowFlow 不允许一次检索混用不同嵌入模型（code 102）：按模型分组各查一次，再按名次融合。"""
    stub = make_stub()
    stub.embedding_models = {"ds1": "bge-m3", "ds2": "text-embedding-v4"}
    hits = await source(stub).search(ALICE, "条款", BOTH, 10)
    assert {e.doc_ref for e in hits} == {"ds1/contract", "ds2/secret"}
    assert [e.span_ref for e in hits[:2]] == ["contract-c1", "secret-c1"], "两组的第一名排在最前"
    assert len(retrieval_requests(stub)) == 3, "先合并查一次被拒，再按模型各查一次"


async def test_per_model_search_keeps_the_readable_groups_and_drops_the_denied():
    stub = make_stub()
    stub.embedding_models = {"ds1": "bge-m3", "ds2": "text-embedding-v4"}
    hits = await source(stub, datasets=()).search(ALICE, "条款", BOTH, 10)  # 开放范围留空也一样
    assert {e.doc_ref for e in hits} == {"ds1/contract", "ds2/secret"}
    bob = await source(stub).search(BOB, "条款", BOTH, 10)  # bob 读不了 ds2：那一组整组去掉
    assert {e.doc_ref for e in bob} == {"ds1/contract"}


async def test_same_embedding_model_is_still_one_request():
    stub = make_stub()
    await source(stub).search(ALICE, "条款", BOTH, 10)
    assert len(retrieval_requests(stub)) == 1


async def test_same_passage_from_several_datasets_is_returned_once():
    """同一份文档传进了多个知识库时，内容相同的片段只留第一条，不浪费引用编号。"""
    stub = make_stub()
    dup = chunk("ds2", "contract-copy", 1, "contract 第1段")
    stub.chunks = {"ds1": [chunk("ds1", "contract", 1), chunk("ds1", "contract", 2)], "ds2": [dup]}
    dup["document_keyword"] = "contract.pdf"
    hits = await source(stub).search(ALICE, "条款", BOTH, 10)
    assert [e.span_ref for e in hits] == ["contract-c1", "contract-c2"]


async def test_read_explains_when_knowflow_lists_no_chunks_for_a_parsed_document():
    """KnowFlow 已知问题：共享知识库按顺序列分块返回 0 条（文档其实有内容）。不给空正文，明说改用检索。"""
    stub = make_stub()
    stub.documents = {"contract": []}
    stub.chunk_counts = {"contract": 16}
    with pytest.raises(SourceUnavailable, match="改用 knowledge_search"):
        await source(stub).read(ALICE, "ds1/contract", None, 1000)
