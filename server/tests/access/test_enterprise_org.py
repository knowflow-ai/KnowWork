"""企业组织：目录同步来的部门树全系统一份，物化进各空间的权限规则；空间按成员规则自动加人。

模拟的组织（来源 ad）：
    corp ─┬─ hq ─┬─ legal（carol、erin）
          │      └─ hr（frank）
          └─ rnd ─┬─ platform（dave、gina）
                  └─ app（hank）
ws1 的成员：alice（所有者）、bob（管理员）、carol、dave（成员）。erin、frank、gina、hank 不是 ws1 的成员。
"""

import pytest

from knowwork_server.access.enterprise import OrgDepartmentInput as Dept
from knowwork_server.access.enterprise import OrgMembershipInput as Member
from knowwork_server.access.errors import Conflict, NotFound
from knowwork_server.access.vocab import InvalidInput

pytestmark = pytest.mark.anyio
SOURCE = "ad"

TREE = (
    Dept("corp", None, "集团"),
    Dept("hq", "corp", "总部"),
    Dept("legal", "hq", "法务部"),
    Dept("hr", "hq", "人力部"),
    Dept("rnd", "corp", "研发中心"),
    Dept("platform", "rnd", "平台组"),
    Dept("app", "rnd", "应用组"),
)
PEOPLE = (
    Member("carol", "legal", True), Member("erin", "legal", True), Member("frank", "hr", True),
    Member("dave", "platform", True), Member("gina", "platform", True), Member("hank", "app", True),
)


def _moved(people, user_id: str, department: str):
    return tuple(Member(m.user_id, department, True) if m.user_id == user_id else m for m in people)


async def _ids(service, ws) -> dict[str, str]:
    """外部 ID → 企业部门 ID（授权、成员规则用的是后者）。"""
    return {d.external_id: d.id for d in await service.org_departments("alice", ws)}


@pytest.fixture
async def org(service, ws):
    await service.replace_org(SOURCE, TREE, PEOPLE)
    return await _ids(service, ws)


async def test_the_tree_is_listed_for_pickers(service, ws, org):
    listed = {d.external_id: d for d in await service.org_departments("carol", ws)}
    assert listed["legal"].parent_id == org["hq"] and listed["corp"].parent_id is None
    assert listed["legal"].name == "法务部" and listed["legal"].source_id == SOURCE
    assert listed["platform"].member_count == 2


async def test_a_grant_to_a_department_reaches_its_members_in_this_workspace_only(service, ws, org):
    await service.grant("bob", ws, f"org:{org['legal']}", "skill:contract", "use")
    assert service.check("carol", ws, "skill:contract", "use")
    assert not service.check("dave", ws, "skill:contract", "use")
    # erin 在法务部，但不是 ws1 的成员：企业部门只在她所在的空间里替她生效
    assert not service.check("erin", ws, "skill:contract", "use")


async def test_a_grant_to_a_parent_includes_sub_departments(service, ws, org):
    await service.grant("bob", ws, f"org:{org['corp']}", "skill:all-hands", "use")
    assert service.check("carol", ws, "skill:all-hands", "use") and service.check("dave", ws, "skill:all-hands", "use")


async def test_transfer_follows_the_directory(service, ws, org):
    await service.grant("bob", ws, f"org:{org['legal']}", "skill:contract", "use")
    await service.grant("bob", ws, f"org:{org['platform']}", "skill:infra", "use")
    change = await service.replace_org(SOURCE, TREE, _moved(PEOPLE, "carol", "platform"))
    assert change.memberships_added == 1 and change.memberships_removed == 1
    assert not service.check("carol", ws, "skill:contract", "use")
    assert service.check("carol", ws, "skill:infra", "use")


async def test_rename_and_move_keep_the_department_and_its_grants(service, ws, org):
    await service.grant("bob", ws, f"org:{org['legal']}", "skill:contract", "use")
    tree = tuple(Dept("legal", "corp", "法务合规部") if d.external_id == "legal" else d for d in TREE)
    change = await service.replace_org(SOURCE, tree, PEOPLE)
    assert change.renamed == 1 and change.moved == 1
    after = await _ids(service, ws)
    assert after["legal"] == org["legal"] and service.check("carol", ws, "skill:contract", "use")


async def test_a_department_gone_from_the_directory_is_removed_softly(service, ws, org):
    await service.grant("bob", ws, f"org:{org['legal']}", "skill:contract", "use")
    tree = tuple(d for d in TREE if d.external_id != "legal")
    people = tuple(m for m in PEOPLE if m.department_external_id != "legal")
    change = await service.replace_org(SOURCE, tree, people)
    assert change.removed == 1
    assert "legal" not in await _ids(service, ws)
    assert not service.check("carol", ws, "skill:contract", "use")
    # 授权本身还在（界面标「已从目录移除」），目录里恢复了就又生效
    assert any(g.subject == f"org:{org['legal']}" for g in await service.list_grants("bob", ws))
    await service.replace_org(SOURCE, TREE, PEOPLE)
    assert (await _ids(service, ws))["legal"] == org["legal"]
    assert service.check("carol", ws, "skill:contract", "use")


async def test_people_joining_a_workspace_later_bring_their_departments(service, ws, org):
    await service.grant("bob", ws, f"org:{org['hr']}", "skill:payroll", "use")
    assert not service.check("frank", ws, "skill:payroll", "use")
    await service.add_member("alice", ws, "frank", "editor")
    assert service.check("frank", ws, "skill:payroll", "use")
    await service.remove_member("alice", ws, "frank")
    assert not service.check("frank", ws, "skill:payroll", "use")


async def test_a_new_workspace_gets_the_tree(service, ws, org):
    await service.create_workspace(workspace_id="ws3", name="空间三", owner_user_id="erin")
    await service.grant("erin", "ws3", f"org:{org['hq']}", "skill:s", "use")
    assert service.check("erin", "ws3", "skill:s", "use")


async def test_grants_need_an_existing_department(service, ws, org):
    with pytest.raises(NotFound):
        await service.grant("bob", ws, "org:od_missing", "skill:s", "use")


async def test_member_rules_add_and_remove_people_with_the_directory(service, ws, org):
    await service.create_workspace(workspace_id="ws2", name="研发空间", owner_user_id="alice")
    await service.add_member("alice", "ws2", "carol", "editor")  # 手工加的，不归规则管
    rule = await service.add_member_rule("alice", "ws2", org["rnd"], include_children=True, role="editor")
    members = {m.user_id: m for m in await service.list_members("alice", "ws2")}
    assert {u for u, m in members.items() if m.origin == "rule"} == {"dave", "gina", "hank"}
    assert members["carol"].origin == "manual"
    # hank 调去法务部：被规则带走；carol 调进研发中心：她是手工成员，照旧
    people = _moved(_moved(PEOPLE, "hank", "legal"), "carol", "platform")
    await service.replace_org(SOURCE, TREE, people)
    members = {m.user_id: m for m in await service.list_members("alice", "ws2")}
    assert "hank" not in members and members["carol"].origin == "manual"
    assert not service.check("hank", "ws2", "workspace", "use")
    await service.remove_member_rule("alice", "ws2", rule.id)
    assert {m.user_id for m in await service.list_members("alice", "ws2")} == {"alice", "carol"}


async def test_only_this_department_when_children_are_excluded(service, ws, org):
    await service.add_member_rule("alice", ws, org["corp"], include_children=False, role="editor")
    # corp 自己下面没有直接挂人
    assert {m.user_id for m in await service.list_members("alice", ws) if m.origin == "rule"} == set()


async def test_the_highest_role_wins_and_a_manual_role_change_takes_the_member_over(service, ws, org):
    await service.add_member_rule("alice", ws, org["rnd"], include_children=True, role="editor")
    await service.add_member_rule("alice", ws, org["platform"], include_children=True, role="admin")
    members = {m.user_id: m for m in await service.list_members("alice", ws)}
    assert members["gina"].role == "admin" and members["hank"].role == "editor"
    # dave 本来就是手工成员：规则不改他的角色
    assert members["dave"].origin == "manual" and members["dave"].role == "editor"
    await service.set_member_role("alice", ws, "hank", "admin")
    hank = next(m for m in await service.list_members("alice", ws) if m.user_id == "hank")
    assert hank.origin == "manual" and hank.role == "admin"
    await service.replace_org(SOURCE, TREE, _moved(PEOPLE, "hank", "legal"))
    assert "hank" in {m.user_id for m in await service.list_members("alice", ws)}


async def test_rule_members_are_not_removed_by_hand(service, ws, org):
    await service.add_member_rule("alice", ws, org["platform"], include_children=True, role="editor")
    with pytest.raises(Conflict):
        await service.remove_member("alice", ws, "gina")


async def test_only_admins_manage_member_rules(service, ws, org):
    from knowwork_server.access.errors import AccessDenied

    with pytest.raises(AccessDenied):
        await service.add_member_rule("carol", ws, org["rnd"], include_children=True, role="editor")
    with pytest.raises(InvalidInput):
        await service.add_member_rule("alice", ws, org["rnd"], include_children=True, role="owner")
    rule = await service.add_member_rule("bob", ws, org["rnd"], include_children=True, role="editor")
    assert [r.id for r in await service.member_rules("bob", ws)] == [rule.id]
    with pytest.raises(Conflict):
        await service.add_member_rule("bob", ws, org["rnd"], include_children=False, role="admin")


async def test_sources_do_not_touch_each_other(service, ws, org):
    await service.replace_org("wecom", (Dept("1", None, "企业微信根"),), (Member("carol", "1", True),))
    ids = await _ids(service, ws)
    assert "legal" in ids and "1" in ids
    await service.replace_org("wecom", (), ())
    assert "legal" in await _ids(service, ws)


async def test_bad_snapshots_are_refused_whole(service, ws, org):
    with pytest.raises(InvalidInput):  # 外部 ID 重复
        await service.replace_org(SOURCE, (*TREE, Dept("hq", "corp", "又一个总部")), PEOPLE)
    with pytest.raises(InvalidInput):  # 成环
        await service.replace_org(SOURCE, (Dept("a", "b", "A"), Dept("b", "a", "B")), ())
    with pytest.raises(InvalidInput):  # 成员挂在不存在的部门上
        await service.replace_org(SOURCE, TREE, (Member("carol", "nowhere", True),))
    assert (await _ids(service, ws)) == org  # 什么都没改


async def test_deep_directories_still_authorize(service, ws):
    """权限库的角色链默认只走 10 层；企业目录常常更深，调高了上限（engine.MAX_ROLE_CHAIN）。"""
    chain = tuple(Dept(f"d{i}", f"d{i - 1}" if i else None, f"第{i}层") for i in range(25))
    await service.replace_org(SOURCE, chain, (Member("carol", "d24", True),))
    ids = await _ids(service, ws)
    await service.grant("bob", ws, f"org:{ids['d0']}", "skill:top", "use")
    assert service.check("carol", ws, "skill:top", "use")
    too_deep = tuple(Dept(f"d{i}", f"d{i - 1}" if i else None, f"第{i}层") for i in range(31))
    with pytest.raises(InvalidInput):
        await service.replace_org(SOURCE, too_deep, ())


async def test_another_process_sees_the_change(service, ws, org, access_db):
    """同步在 worker 进程里写规则：业务服务进程收到通知后重新加载，判定跟着变。"""
    import asyncio

    from knowwork_server.access.service import AccessService
    from knowwork_server.db.base import make_engine, make_sessionmaker

    engine = make_engine(access_db.app_url)
    api = AccessService(make_sessionmaker(engine))
    assert await api.reload()
    stop, ready = asyncio.Event(), asyncio.Event()
    listener = asyncio.create_task(api.follow_changes(engine, stop, ready=ready))
    try:
        await asyncio.wait_for(ready.wait(), 10)
        await service.grant("bob", ws, f"org:{org['legal']}", "skill:contract", "use")
        for _ in range(100):
            if api.check("carol", ws, "skill:contract", "use"):
                break
            await asyncio.sleep(0.05)
        assert api.check("carol", ws, "skill:contract", "use")
    finally:
        stop.set()
        await listener
        await engine.dispose()


async def test_a_failed_reload_is_retried(service, ws, org, access_db, monkeypatch):
    """跟着别的进程重新加载时数据库抖了一下：判定器先全部拒绝，隔几秒自己再加载，不用等下一次有人改权限。"""
    import asyncio

    from knowwork_server.access import store
    from knowwork_server.access.service import AccessService
    from knowwork_server.db.base import make_engine, make_sessionmaker

    engine = make_engine(access_db.app_url)
    api = AccessService(make_sessionmaker(engine))
    assert await api.reload()
    real, failures = store.load_all, []

    async def flaky(sessionmaker):
        if failures:
            failures.pop()
            raise ConnectionError("数据库抖了一下")
        return await real(sessionmaker)

    monkeypatch.setattr(store, "load_all", flaky)
    stop, ready = asyncio.Event(), asyncio.Event()
    listener = asyncio.create_task(api.follow_changes(engine, stop, ready=ready, retry_seconds=0.1))
    try:
        await asyncio.wait_for(ready.wait(), 10)
        await asyncio.sleep(0.5)  # 连上时补的那次加载先做完
        failures.append(1)        # 收到下面这次改动的通知后，第一次加载失败；之后没有别的通知了
        await service.grant("bob", ws, f"org:{org['legal']}", "skill:contract", "use")
        for _ in range(100):
            if api.check("carol", ws, "skill:contract", "use"):
                break
            await asyncio.sleep(0.05)
        assert not failures and api.check("carol", ws, "skill:contract", "use")
    finally:
        stop.set()
        await listener
        await engine.dispose()


async def test_holding_removals_keeps_missing_departments_and_memberships(service, ws, org):
    """误删保护挂起时：快照里没了的部门先留着，判离职但没停用的人照旧挂在原部门（进空间、授权都不变）。"""
    await service.grant("bob", ws, f"org:{org['legal']}", "skill:contract", "use")
    tree = tuple(d for d in TREE if d.external_id != "legal")
    people = tuple(m for m in PEOPLE if m.department_external_id != "legal" and m.user_id != "dave")
    change = await service.replace_org(SOURCE, tree, people, keep_missing_departments=True,
                                       keep_memberships_of=("carol", "dave"))
    assert change.removed == 0
    assert "legal" in await _ids(service, ws)
    assert service.check("carol", ws, "skill:contract", "use")
    listed = {d.external_id: d for d in await service.org_departments("alice", ws)}
    assert listed["legal"].member_count == 1  # carol 留着，erin 走了
    assert listed["platform"].member_count == 2  # dave 留着


async def test_the_state_of_a_source(service, ws, org):
    state = await service.org_source_state(SOURCE)
    assert state.external_ids == frozenset(d.external_id for d in TREE)
    assert state.roots == (org["corp"],)
    empty = await service.org_source_state("nothing")
    assert empty.external_ids == frozenset() and empty.roots == ()


async def test_ensure_member_rule_is_idempotent(service, ws, org):
    assert await service.ensure_member_rule(ws, org["corp"], include_children=True, role="editor") is True
    assert await service.ensure_member_rule(ws, org["corp"], include_children=True, role="editor") is False
    assert {m.user_id for m in await service.list_members("alice", ws) if m.origin == "rule"} \
        == {"erin", "frank", "gina", "hank"}


async def test_a_service_without_a_mirror_still_writes_and_notifies(access_db, service, ws, org):
    """worker 里的权限服务只写库、不在内存里维护判定器（它不判定）；通知照发，业务服务据此重新加载。"""
    from knowwork_server.access.service import AccessService
    from knowwork_server.db.base import make_engine, make_sessionmaker

    engine = make_engine(access_db.app_url)
    worker = AccessService(make_sessionmaker(engine), mirror=False)
    try:
        await worker.replace_org(SOURCE, TREE, _moved(PEOPLE, "carol", "platform"))
        assert not worker.engine.healthy  # 没加载过、也不加载
        assert await service.reload()
        await service.grant("bob", ws, f"org:{org['platform']}", "skill:infra", "use")
        assert service.check("carol", ws, "skill:infra", "use")
    finally:
        await engine.dispose()
