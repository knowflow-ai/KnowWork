"""资源授权、判定、可见集合与 fail-closed。"""

import pytest

from knowwork_server.access.errors import AccessDenied, NotFound
from knowwork_server.access.rls import set_workspace
from knowwork_server.access.vocab import InvalidInput
from knowwork_server.db.base import make_engine, make_sessionmaker

pytestmark = pytest.mark.anyio


async def test_grant_change_and_revoke(service, ws):
    view = await service.grant("bob", ws, "user:dave", "skill:s1", "edit")
    assert (view.subject, view.resource, view.action) == ("user:dave", "skill:s1", "edit")
    assert service.check("dave", ws, "skill:s1", "use")
    assert not service.check("dave", ws, "skill:s1", "owner")
    await service.grant("bob", ws, "user:dave", "skill:s1", "use")  # 降档
    assert not service.check("dave", ws, "skill:s1", "edit")
    await service.grant("bob", ws, "user:dave", "skill:s1", "use")  # 重复授权幂等
    assert [g.action for g in await service.list_grants("bob", ws, subject="user:dave")] == ["use"]
    await service.revoke("bob", ws, "user:dave", "skill:s1")
    assert not service.check("dave", ws, "skill:s1", "use")
    with pytest.raises(NotFound):
        await service.revoke("bob", ws, "user:dave", "skill:s1")


async def test_role_and_wildcard_grants(service, ws):
    await service.grant("alice", ws, "role:viewer", "tool:*", "use")
    assert service.check("dave", ws, "tool:anything", "use")
    assert service.visible_resources("dave", ws, "tool", "use").all_
    assert not service.visible_resources("dave", ws, "skill", "use").all_
    assert service.check("bob", ws, "skill:x", "owner")  # admin 默认管理全部资源
    assert service.check("alice", ws, "knowledge_base:ks_a.x", "owner")
    assert not service.check("carol", ws, "skill:x", "use")


async def test_tool_prefix_wildcard_covers_one_mcp_server(service, ws):
    """按 MCP 服务授权：tool:mcp__gh__* 覆盖该服务的全部工具，不波及别的服务；收回后立即失效。"""
    group = await service.create_group("alice", ws, "研发")
    await service.add_group_member("alice", ws, group, "dave")
    await service.grant("alice", ws, f"group:{group}", "tool:mcp__gh__*", "use")
    await service.grant("alice", ws, "user:carol", "tool:mcp__gh__*", "use")  # 同一资源授给两个主体
    assert service.check("dave", ws, "tool:mcp__gh__create_issue", "use")
    assert not service.check("dave", ws, "tool:mcp__ghx__create_issue", "use"), "前缀要带上服务名后的 __"
    assert not service.check("dave", ws, "tool:mcp__jira__search", "use")
    assert not service.check("dave", ws, "tool:mcp__gh__create_issue", "edit")
    visible = service.visible_resources("dave", ws, "tool", "use")
    assert visible.allows("mcp__gh__list") and not visible.allows("mcp__jira__list")
    await service.revoke("alice", ws, f"group:{group}", "tool:mcp__gh__*")
    assert not service.check("dave", ws, "tool:mcp__gh__create_issue", "use")
    assert service.check("carol", ws, "tool:mcp__gh__create_issue", "use"), "另一条同资源授权仍在"
    assert await service.reload()
    assert service.check("carol", ws, "tool:mcp__gh__x", "use"), "整体重载后判定不变"


async def test_models_are_not_granted(service, ws):
    """模型不按人授权（2026-10-23 去掉了模型分组）：没有这种资源，角色也不带模型的默认权限。"""
    with pytest.raises(InvalidInput):
        await service.grant("alice", ws, "user:dave", "model_group:m1", "use")
    assert await service.list_grants("alice", ws) == ()


async def test_prefix_wildcard_is_only_for_tools_and_knowledge_bases(service, ws):
    with pytest.raises(InvalidInput):
        await service.grant("alice", ws, "user:dave", "skill:report*", "use")
    with pytest.raises(InvalidInput):
        await service.grant("alice", ws, "user:dave", "tool:bad id*", "use")


async def test_knowledge_base_grants_cover_one_base_or_one_source(service, ws):
    """知识库授在 <知识源id>.<知识库id> 上；<知识源id>.* 覆盖这个来源下的全部，不波及别的来源。"""
    await service.grant("alice", ws, "user:dave", "knowledge_base:ks_a.ds1", "use")
    await service.grant("alice", ws, "user:carol", "knowledge_base:ks_a.*", "use")
    assert service.check("dave", ws, "knowledge_base:ks_a.ds1", "use")
    assert not service.check("dave", ws, "knowledge_base:ks_a.ds2", "use")
    assert service.check("carol", ws, "knowledge_base:ks_a.ds2", "use")
    assert not service.check("carol", ws, "knowledge_base:ks_ab.ds1", "use"), "前缀带上来源后的点号"
    visible = service.visible_resources("carol", ws, "knowledge_base", "use")
    assert visible.allows("ks_a.anything") and not visible.allows("ks_b.ds1")
    for resource in ("knowledge_base:ks_a", "knowledge_base:ks_a*", "knowledge_base:ks_a.ds*",
                     "knowledge_base:ks_a.x.y"):
        with pytest.raises(InvalidInput, match="知识库须写成"):
            await service.grant("alice", ws, "user:dave", resource, "use")


async def test_knowledge_sources_are_not_grantable(service, ws):
    """知识源只表示谁能改连接，由空间角色的默认规则给；授权接口不接受它。"""
    with pytest.raises(InvalidInput, match="知识源不单独授权"):
        await service.grant("alice", ws, "user:dave", "knowledge_source:ks_a", "use")
    assert service.check("bob", ws, "knowledge_source:ks_a", "owner"), "管理员默认是全部知识源的所有者"
    assert not service.check("carol", ws, "knowledge_source:ks_a", "use")


async def test_purge_grants_runs_in_the_callers_transaction(service, ws, access_db):
    """删除资源时在调用方事务里清掉它的授权；提交后 settle 才生效，回滚则授权原样保留。"""
    await service.grant("alice", ws, "user:dave", "knowledge_base:ks_a.ds1", "use")
    await service.grant("alice", ws, "user:carol", "knowledge_base:ks_a.*", "use")
    await service.grant("alice", ws, "user:carol", "knowledge_base:ks_b.ds1", "use")
    await service.grant("alice", ws, "user:dave", "tool:mcp__gh__*", "use")
    await service.grant("alice", ws, "user:dave", "tool:mcp__ghx__run", "use")
    engine = make_engine(access_db.app_url)
    sessionmaker = make_sessionmaker(engine)

    async def purge(**kwargs):
        async with sessionmaker() as session, session.begin():
            await set_workspace(session, ws)
            return await service.purge_grants(session, ws, **kwargs)

    with pytest.raises(RuntimeError):
        async with sessionmaker() as session, session.begin():
            await set_workspace(session, ws)
            await service.purge_grants(session, ws, prefixes=("knowledge_base:ks_a.",))
            raise RuntimeError("业务删除失败")
    assert len(await service.list_grants("alice", ws)) == 5, "回滚后授权都还在"
    change = await purge(prefixes=("knowledge_base:ks_a.",))
    assert service.check("dave", ws, "knowledge_base:ks_a.ds1", "use"), "提交了但还没 settle"
    await service.settle(change)
    assert not service.check("dave", ws, "knowledge_base:ks_a.ds1", "use")
    assert not service.check("carol", ws, "knowledge_base:ks_a.ds9", "use")
    assert service.check("carol", ws, "knowledge_base:ks_b.ds1", "use"), "别的来源不受影响"
    await service.settle(await purge(prefixes=("tool:mcp__gh__",)))
    assert not service.check("dave", ws, "tool:mcp__gh__x", "use")
    assert service.check("dave", ws, "tool:mcp__ghx__run", "use"), "前缀里的下划线按字面匹配"
    assert service.check("bob", ws, "knowledge_base:ks_a.ds1", "owner"), "默认规则不删"
    assert service.check("bob", ws, "tool:mcp__gh__x", "owner")
    assert await service.reload()
    assert [g.resource for g in await service.list_grants("alice", ws)] == ["knowledge_base:ks_b.ds1",
                                                                          "tool:mcp__ghx__run"]
    await engine.dispose()


async def test_visible_resources_unions_all_sources(service, ws):
    group = await service.create_group("alice", ws, "g")
    dept = await service.create_department("alice", ws, "d")
    await service.add_group_member("alice", ws, group, "dave")
    await service.add_department_member("alice", ws, dept, "dave")
    await service.grant("alice", ws, "user:dave", "skill:a", "use")
    await service.grant("alice", ws, f"group:{group}", "skill:b", "edit")
    await service.grant("alice", ws, f"dept:{dept}", "skill:c", "use")
    await service.grant("alice", ws, "user:carol", "skill:d", "use")
    visible = service.visible_resources("dave", ws, "skill", "use")
    assert not visible.all_ and visible.ids == {"a", "b", "c"}
    assert visible.allows("a") and not visible.allows("d")
    assert service.visible_resources("dave", ws, "skill", "edit").ids == {"b"}
    assert service.visible_resources("dave", ws, "skill", "owner").ids == frozenset()
    assert service.visible_resources("alice", ws, "skill", "owner").all_


async def test_grant_validation_and_permissions(service, ws):
    with pytest.raises(AccessDenied):
        await service.grant("carol", ws, "user:dave", "skill:s1", "use")
    with pytest.raises(AccessDenied):
        await service.list_grants("dave", ws)
    with pytest.raises(NotFound):
        await service.grant("bob", ws, "user:outsider", "skill:s1", "use")
    with pytest.raises(NotFound):
        await service.grant("bob", ws, "group:nope", "skill:s1", "use")
    for subject, resource, action in [("admin", "skill:s1", "use"), ("user:dave", "workspace", "use"),
                                      ("user:dave", "*", "use"), ("user:dave", "skill:s1", "delete"),
                                      ("role:root", "skill:s1", "use"), ("user:dave", "secret:x", "use")]:
        with pytest.raises(InvalidInput):
            await service.grant("bob", ws, subject, resource, action)


async def test_check_rejects_bad_input_instead_of_raising(service, ws):
    assert not service.check("dave", ws, "skill:*", "use")
    assert not service.check("dave", ws, "skill:s1", "fly")
    assert not service.check("bad id!", ws, "skill:s1", "use")
    assert not service.check("dave", ws, "nonsense", "use")
    assert not service.visible_resources("dave", ws, "secret", "use").ids
    assert not service.visible_resources("dave", ws, "skill", "fly").ids


async def test_fail_closed_when_engine_unavailable(service, ws):
    await service.grant("alice", ws, "user:dave", "skill:s1", "use")
    service.engine.invalidate()
    assert not service.check("alice", ws, "workspace", "owner")
    assert not service.check("dave", ws, "skill:s1", "use")
    assert service.visible_resources("alice", ws, "skill", "use").ids == frozenset()
    with pytest.raises(AccessDenied):
        await service.add_member("alice", ws, "erin", "viewer")
    assert await service.reload()
    assert service.check("dave", ws, "skill:s1", "use")


async def test_check_errors_deny(service, ws, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("matcher exploded")

    monkeypatch.setattr(service.engine, "_enforce", boom)
    assert not service.check("alice", ws, "workspace", "use")
    enforcer = service.engine._enforcer
    monkeypatch.setattr(enforcer, "get_implicit_roles_for_user", boom)
    assert not service.visible_resources("alice", ws, "skill", "use").all_


async def test_failed_apply_triggers_reload(service, ws, monkeypatch):
    original = service.engine.apply

    def flaky(change):
        monkeypatch.setattr(service.engine, "apply", original)
        service.engine.invalidate()
        raise RuntimeError("apply failed")

    monkeypatch.setattr(service.engine, "apply", flaky)
    await service.grant("alice", ws, "user:dave", "skill:s9", "use")
    assert service.engine.healthy
    assert service.check("dave", ws, "skill:s9", "use")


async def test_failed_reload_keeps_denying(service, ws, monkeypatch):
    from knowwork_server.access import core

    async def broken(_):
        raise RuntimeError("db down")

    monkeypatch.setattr(core.store, "load_all", broken)
    assert not await service.reload()
    assert not service.engine.healthy
    assert not service.check("alice", ws, "workspace", "use")


async def test_rolled_back_transaction_does_not_touch_engine(service, ws, monkeypatch):
    from knowwork_server.access import service as service_module

    async def fail(*args, **kwargs):
        raise RuntimeError("subject lookup failed")

    monkeypatch.setattr(service_module.AccessService, "_require_subject", fail)
    with pytest.raises(RuntimeError):
        await service.grant("alice", ws, "user:dave", "skill:s1", "use")
    assert not service.check("dave", ws, "skill:s1", "use")
