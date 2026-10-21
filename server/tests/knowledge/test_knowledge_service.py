import pytest
from sqlalchemy import text

from knowwork_server.knowledge.errors import (
    AccessDenied,
    InvalidInput,
    NoReadableKnowledge,
    NotFound,
    ScopeDenied,
    SourceUnavailable,
)
from knowwork_server.knowledge.models import Page
from knowwork_server.knowledge.schemas import SourceCreate, SourcePatch

from .knowflow_stub import SERVICE_KEY

pytestmark = pytest.mark.anyio


async def create(env, *, name="合同库", mode="assertion", datasets=("ds1", "ds2"), key=SERVICE_KEY, actor="alice",
                 account_key=None):
    body = SourceCreate(name=name, base_url="http://kf.local", service_key=key, account_key=account_key,
                        config={"auth_mode": mode, "dataset_ids": datasets})
    return await env.knowledge.admin.create(actor, "ws1", body)


async def create_shared(env, *, datasets=("ds1", "ds2"), account_key="ragflow-alicekey"):
    """统一账号：用 alice 的 KnowFlow Key（她能读 ds1、ds2）。"""
    return await create(env, name="共享库", mode="shared", datasets=datasets, key=None, account_key=account_key)


async def grant_use(env, user, source_id, kb="*"):
    await env.access.grant("alice", "ws1", f"user:{user}", f"knowledge_base:{source_id}.{kb}", "use")


async def search(env, user, query="条款", **kwargs):
    caller = await env.knowledge.service.caller("ws1", user)
    return await env.knowledge.service.search(caller, query, **kwargs)


def retrievals(env):
    return [r for r in env.stub.requests if r.url.path == "/api/v1/retrieval"]


async def test_secret_is_encrypted_and_never_returned(env):
    detail = await create(env)
    assert detail.service_key.configured and detail.service_key.last4 == SERVICE_KEY[-4:]
    assert not detail.account_key.configured
    assert SERVICE_KEY not in detail.model_dump_json()
    async with env.sessionmaker() as session:
        await session.execute(text("SELECT set_config('app.workspace_id', 'ws1', false)"))
        stored = await session.scalar(text("SELECT secret_ciphertext FROM knowledge_source"))
    assert stored and SERVICE_KEY not in stored


async def test_two_users_get_evidence_limited_by_their_own_knowflow_permissions(env):
    source = await create(env)
    await grant_use(env, "carol", source.id)
    alice = await search(env, "alice")
    bob = await search(env, "bob")
    assert {e.doc_ref for e in alice.evidence} == {"ds1/contract", "ds2/secret"}
    assert {e.doc_ref for e in bob.evidence} == {"ds1/contract"}
    with pytest.raises(NoReadableKnowledge, match="都没有读取权限"):
        await search(env, "carol")


async def test_rbac_on_the_platform_side_comes_first(env):
    source = await create(env)
    with pytest.raises(NoReadableKnowledge, match="没有使用权限"):
        await search(env, "carol")  # editor 默认没有知识库的使用权
    await grant_use(env, "carol", source.id)
    with pytest.raises(NotFound):
        await env.knowledge.admin.get("dave", "ws1", source.id)
    assert (await env.knowledge.admin.get("carol", "ws1", source.id)).model_dump().get("base_url") is None


async def test_grant_on_one_base_narrows_before_the_request_is_sent(env):
    """统一账号下 KnowFlow 看到的总是同一个账号，范围只靠 KnowWork 的知识库授权（第 5.4 节第 1 层）。"""
    source = await create_shared(env)
    await grant_use(env, "carol", source.id, "ds1")
    hits = await search(env, "carol")
    assert {e.doc_ref for e in hits.evidence} == {"ds1/contract"}
    assert [r.read().decode().count("ds2") for r in retrievals(env)] == [0], "ds2 没授权，不发给知识源"
    assert {e.kb_id for e in (await search(env, "bob")).evidence} == {"ds1", "ds2"}, "管理员默认能查全部知识库"


async def test_named_base_outside_grants_or_range_is_refused_not_sent(env):
    source = await create_shared(env, datasets=("ds1",))
    await grant_use(env, "carol", source.id, "ds1")
    for named in (f"{source.id}.ds2", "ks_missing.ds1"):
        with pytest.raises(NoReadableKnowledge, match="不存在或你没有使用权限"):
            await search(env, "carol", bases=(named,))
    with pytest.raises(NoReadableKnowledge, match="不存在或你没有使用权限"):
        await search(env, "bob", bases=(f"{source.id}.ds2",))  # 管理员也不能越过开放范围
    assert retrievals(env) == []
    assert {e.kb_id for e in (await search(env, "carol", bases=(f"{source.id}.ds1",))).evidence} == {"ds1"}


async def test_empty_open_range_lists_what_the_caller_reads_in_knowflow(env):
    await create(env, datasets=())
    assert {e.doc_ref for e in (await search(env, "bob")).evidence} == {"ds1/contract"}
    assert {e.doc_ref for e in (await search(env, "alice")).evidence} == {"ds1/contract", "ds2/secret"}


async def test_user_without_knowflow_account_is_refused_with_reason(env):
    source = await create(env)
    await grant_use(env, "erin", source.id)
    with pytest.raises(NoReadableKnowledge, match="没有对应账号"):
        await search(env, "erin")
    await grant_use(env, "dave", source.id)
    with pytest.raises(NoReadableKnowledge, match="没有邮箱"):
        await search(env, "dave")  # 停用账号查不到邮箱


async def test_bound_but_unreadable_is_refused_not_silently_empty(env):
    with pytest.raises(NoReadableKnowledge, match="还没有配置知识源"):
        await search(env, "alice")
    await create(env)
    with pytest.raises(NoReadableKnowledge, match="没有选择知识库"):
        await search(env, "alice", bases=())
    with pytest.raises(NoReadableKnowledge, match="不存在"):
        await search(env, "alice", bases=("ks_missing.*",))
    with pytest.raises(InvalidInput, match="写法不对"):
        await search(env, "alice", bases=("bad id",))


async def test_partial_failures_are_reported_in_notes_and_results_are_merged(env):
    good = await create(env)
    other = await create(env, name="制度库", datasets=("ds2",))
    off = await create(env, name="旧库")
    await env.knowledge.admin.update("alice", "ws1", off.id, SourcePatch(enabled=False))
    result = await search(env, "bob", bases=(f"{good.id}.*", f"{other.id}.*", f"{off.id}.*"))
    assert {e.source_id for e in result.evidence} == {good.id}
    assert any("制度库" in n and "读取权限" in n for n in result.notes)
    assert any("旧库" in n and "停用" in n for n in result.notes)
    both = await search(env, "alice", bases=(good.id, other.id), top_n=10)  # 只写知识源 id 的旧写法 = 整个来源
    assert [e.source_id for e in both.evidence][:2] == [good.id, other.id], "RRF 交错合并两个源"


async def test_all_sources_down_is_reported_as_unavailable(env):
    await create(env)
    env.stub.down = True
    with pytest.raises(SourceUnavailable, match="暂时不可用"):
        await search(env, "alice")


async def test_admin_permissions_and_validation(env):
    with pytest.raises(AccessDenied):
        await create(env, actor="carol")
    with pytest.raises(InvalidInput, match="服务密钥"):
        await create(env, key=None)
    source = await create(env, actor="bob")
    await grant_use(env, "carol", source.id)
    with pytest.raises(AccessDenied):
        await env.knowledge.admin.update("carol", "ws1", source.id, SourcePatch(name="改名"))
    renamed = await env.knowledge.admin.update("bob", "ws1", source.id, SourcePatch(name="改名"))
    assert renamed.name == "改名" and renamed.service_key.configured
    with pytest.raises(AccessDenied):
        await env.knowledge.admin.delete("carol", "ws1", source.id)
    await env.knowledge.admin.delete("bob", "ws1", source.id)
    with pytest.raises(NotFound):
        await env.knowledge.admin.get("bob", "ws1", source.id)


async def test_deleting_a_source_removes_its_base_grants(env):
    """删除知识源时同一事务删掉它下面的知识库授权，授权页不会留下孤儿授权（技术方案 5.2 节）。"""
    gone = await create(env)
    kept = await create(env, name="制度库")
    await grant_use(env, "carol", gone.id)
    await grant_use(env, "erin", gone.id, "ds1")
    await grant_use(env, "carol", kept.id, "ds2")
    await env.knowledge.admin.delete("bob", "ws1", gone.id)
    assert [g.resource for g in await env.access.list_grants("alice", "ws1")] == [f"knowledge_base:{kept.id}.ds2"]
    assert not env.access.check("carol", "ws1", f"knowledge_base:{gone.id}.ds1", "use")
    assert env.access.check("carol", "ws1", f"knowledge_base:{kept.id}.ds2", "use")


async def test_shared_account_mode_admin(env):
    with pytest.raises(InvalidInput, match="统一账号方式必须配置"):
        await create_shared(env, account_key=None)
    with pytest.raises(InvalidInput, match="不符"):
        await create(env, mode="shared", account_key="ragflow-alicekey")  # 同时填了服务密钥
    source = await create_shared(env)
    assert source.account_key.configured and source.account_key.last4 == "ekey"
    assert not source.service_key.configured and "ragflow-alicekey" not in source.model_dump_json()
    assert (await env.knowledge.admin.probe("bob", "ws1", source.id)).ok
    with pytest.raises(InvalidInput, match="服务密钥"):
        await env.knowledge.admin.update("bob", "ws1", source.id,
                                         SourcePatch(config={"auth_mode": "assertion", "dataset_ids": ["ds1"]}))
    switched = await env.knowledge.admin.update("bob", "ws1", source.id, SourcePatch(
        config={"auth_mode": "assertion", "dataset_ids": []}, service_key=SERVICE_KEY))
    assert switched.service_key.configured and not switched.account_key.configured, "换了方式，密钥跟着换"
    with pytest.raises(ValueError):
        SourcePatch(config={"auth_mode": "api_key"})  # 个人 Key 方式已去掉


async def test_shared_account_with_empty_range_opens_what_the_account_reads(env):
    """统一账号留空开放范围：那个账号读得到的全部；谁能查哪个仍按知识库授权。"""
    source = await create_shared(env, datasets=(), account_key="ragflow-bobkey")  # bob 的账号只读得到 ds1
    assert (await env.knowledge.admin.probe("alice", "ws1", source.id)).ok
    assert {e.kb_id for e in (await search(env, "alice")).evidence} == {"ds1"}, "范围是账号的，不是调用者的"
    await grant_use(env, "carol", source.id)
    assert {e.kb_id for e in (await search(env, "carol")).evidence} == {"ds1"}
    with pytest.raises(NoReadableKnowledge, match="都没有读取权限"):
        await search(env, "carol", bases=(f"{source.id}.ds2",))  # 授了整个来源，但账号读不了 ds2
    assert {s.id: s.bases for s in await env.knowledge.admin.queryable("carol", "ws1")} == {source.id: ("*",)}


async def test_shared_account_probe_reports_bases_the_account_cannot_read(env):
    source = await create_shared(env, account_key="ragflow-bobkey")  # bob 读不了 ds2
    result = await env.knowledge.admin.probe("alice", "ws1", source.id)
    assert not result.ok and "ds2" in result.message


async def test_list_shows_detail_only_to_editors(env):
    a = await create(env, name="甲")
    await create(env, name="乙")
    await grant_use(env, "carol", a.id, "ds1")
    carol = await env.knowledge.admin.list("carol", "ws1")
    assert [s.name for s in carol] == ["甲"] and not hasattr(carol[0], "base_url")
    assert all(hasattr(s, "base_url") for s in await env.knowledge.admin.list("bob", "ws1"))
    assert await env.knowledge.admin.list("dave", "ws1") == []


async def test_catalog_lists_bases_by_source_for_editors(env):
    """授权页的树：以操作者身份列，开放范围里列不出来的也列上（名字用 id）；列不出来时写明原因。"""
    assertion = await create(env, datasets=("ds1", "ds2", "ds9"))
    shared = await create_shared(env, datasets=("ds2",))
    catalog = {c.source_id: c for c in await env.knowledge.admin.catalog("bob", "ws1")}
    assert [(b.id, b.name) for b in catalog[assertion.id].bases] == [
        (f"{assertion.id}.ds1", "库ds1"), (f"{assertion.id}.ds2", "ds2"), (f"{assertion.id}.ds9", "ds9")]
    assert [(b.kb_id, b.name) for b in catalog[shared.id].bases] == [("ds2", "库ds2")], "统一账号用那个账号去列"
    assert await env.knowledge.admin.catalog("carol", "ws1") == ()
    env.stub.down = True
    down = {c.source_id: c for c in await env.knowledge.admin.catalog("bob", "ws1")}
    assert "暂时不可用" in down[assertion.id].error and [b.kb_id for b in down[assertion.id].bases] == ["ds1", "ds2",
                                                                                                    "ds9"]


async def test_queryable_bases_for_the_runtime_profile(env):
    """运行配置快照只用授权与开放范围，不问知识源。"""
    ranged = await create(env, datasets=("ds1", "ds2"))
    open_ = await create(env, name="全开", datasets=())
    await grant_use(env, "carol", ranged.id, "ds2")
    await grant_use(env, "carol", open_.id)
    assert {s.id: s.bases for s in await env.knowledge.admin.queryable("carol", "ws1")} == {
        ranged.id: ("ds2",), open_.id: ("*",)}
    assert {s.id: s.bases for s in await env.knowledge.admin.queryable("bob", "ws1")} == {
        ranged.id: ("ds1", "ds2"), open_.id: ("*",)}
    assert await env.knowledge.admin.queryable("dave", "ws1") == ()
    assert env.stub.requests == []


async def test_probe_fails_when_knowflow_ignores_assertions(env):
    source = await create(env, datasets=())
    env.stub.assertion_supported = False
    result = await env.knowledge.admin.probe("alice", "ws1", source.id)
    assert not result.ok and "没有接受签名断言" in result.message
    with pytest.raises(NoReadableKnowledge, match="没有接受签名断言"):
        await search(env, "alice")


async def test_probe_uses_the_operators_identity(env):
    source = await create(env)
    assert (await env.knowledge.admin.probe("alice", "ws1", source.id)).ok
    env.stub.down = True
    result = await env.knowledge.admin.probe("alice", "ws1", source.id)
    assert not result.ok and "暂时不可用" in result.message


async def test_read_and_list_documents_through_service(env):
    source = await create(env)
    caller = await env.knowledge.service.caller("ws1", "bob")
    first = await env.knowledge.service.read(caller, source.id, "ds1/contract", max_chars=50)
    assert len(first.text) <= 50 and first.next_cursor is not None
    rest = await env.knowledge.service.read(caller, source.id, "ds1/contract", cursor=first.next_cursor.encode(),
                                            max_chars=99999)
    assert (first.text + rest.text).count("甲") == 120 and rest.next_cursor is None
    with pytest.raises(InvalidInput):
        await env.knowledge.service.read(caller, source.id, "ds1/contract", cursor="bad")
    docs = await env.knowledge.service.list_documents(caller, source.id, Page(size=10))
    assert [d.doc_ref for d in docs.items] == ["ds1/contract"]
    other = await env.knowledge.service.caller("ws1", "carol")
    with pytest.raises(NotFound):
        await env.knowledge.service.read(other, source.id, "ds1/contract")
    await env.knowledge.admin.update("alice", "ws1", source.id, SourcePatch(enabled=False))
    with pytest.raises(AccessDenied, match="停用"):
        await env.knowledge.service.read(caller, source.id, "ds1/contract")


async def test_read_checks_the_documents_base_against_grants_and_selection(env):
    source = await create_shared(env)
    await grant_use(env, "carol", source.id, "ds2")
    carol = await env.knowledge.service.caller("ws1", "carol")
    with pytest.raises(NotFound, match="没有使用权限"):
        await env.knowledge.service.read(carol, source.id, "ds1/contract")
    bob = await env.knowledge.service.caller("ws1", "bob")
    assert (await env.knowledge.service.read(bob, source.id, "ds1/contract", bases=(f"{source.id}.*",))).text
    with pytest.raises(ScopeDenied, match="本轮选择"):
        await env.knowledge.service.read(bob, source.id, "ds1/contract", bases=(f"{source.id}.ds2",))


async def test_rows_are_isolated_by_workspace(env):
    await create(env)
    await env.access.create_workspace(workspace_id="ws2", name="空间二", owner_user_id="alice")
    assert await env.knowledge.admin.list("alice", "ws2") == []
