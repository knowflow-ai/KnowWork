"""实验 E5：50 条典型授权用例全部判对；1 万条策略下的判定与可见集合耗时。

看实测数字：uv run pytest tests/access/test_e5.py -o log_cli=true --log-cli-level=INFO
"""

import logging
import statistics
import time

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.access.models import CasbinRule, Department, DepartmentMember, WorkspaceMember
from knowwork_server.access.rules import default_role_policies

pytestmark = pytest.mark.anyio
log = logging.getLogger("e5")


async def _scenario(service):
    """acme：集团 > 研发中心 > 后端组，集团 > 销售部；用户组 法务、客服。beta 是另一个空间。"""
    await service.create_workspace(workspace_id="acme", name="ACME", owner_user_id="o")
    for user, role in [("a", "admin"), ("e", "editor"), ("v", "viewer")] + [(f"u{i}", "viewer") for i in range(1, 8)]:
        await service.add_member("o", "acme", user, role)
    hq = await service.create_department("a", "acme", "集团")
    rd = await service.create_department("a", "acme", "研发中心", hq)
    be = await service.create_department("a", "acme", "后端组", rd)
    sales = await service.create_department("a", "acme", "销售部", hq)
    legal = await service.create_group("a", "acme", "法务")
    cs = await service.create_group("a", "acme", "客服")
    for dept, user in [(be, "u1"), (rd, "u2"), (sales, "u3"), (be, "u5")]:
        await service.add_department_member("a", "acme", dept, user)
    for group, user in [(legal, "u4"), (cs, "u5"), (legal, "u7")]:
        await service.add_group_member("a", "acme", group, user)
    grants = [(f"dept:{hq}", "skill:hq", "use"), (f"dept:{rd}", "skill:rd", "edit"), (f"dept:{be}", "tool:be", "owner"),
              (f"group:{legal}", "knowledge_base:law.*", "use"), (f"group:{cs}", "knowledge_base:cs.faq", "edit"),
              ("user:u6", "skill:solo", "use"), ("role:editor", "skill:*", "use"), (f"dept:{sales}", "tool:crm", "use")]
    for subject, resource, action in grants:
        await service.grant("a", "acme", subject, resource, action)
    await service.create_workspace(workspace_id="beta", name="Beta", owner_user_id="u1")
    await service.add_member("u1", "beta", "u2", "viewer")
    return {"hq": hq, "rd": rd, "be": be, "sales": sales, "legal": legal, "cs": cs}


CASES = [
    # 三级部门继承
    ("u1", "acme", "skill:hq", "use", True), ("u1", "acme", "skill:rd", "edit", True),
    ("u1", "acme", "skill:rd", "use", True), ("u1", "acme", "skill:rd", "owner", False),
    ("u1", "acme", "tool:be", "owner", True), ("u2", "acme", "skill:hq", "use", True),
    ("u2", "acme", "skill:rd", "edit", True), ("u2", "acme", "tool:be", "use", False),
    ("u3", "acme", "skill:hq", "use", True), ("u3", "acme", "skill:rd", "use", False),
    ("u3", "acme", "tool:crm", "use", True), ("u1", "acme", "tool:crm", "use", False),
    # 授权给组
    ("u4", "acme", "knowledge_base:law.contracts", "use", True), ("u4", "acme", "knowledge_base:law.contracts", "edit", False),
    ("u7", "acme", "knowledge_base:law.contracts", "use", True), ("u5", "acme", "knowledge_base:cs.faq", "edit", True),
    ("u5", "acme", "knowledge_base:cs.faq", "owner", False), ("u4", "acme", "knowledge_base:cs.faq", "use", False),
    ("u5", "acme", "tool:be", "owner", True), ("u5", "acme", "knowledge_base:law.contracts", "use", False),
    # 直接授权给个人
    ("u6", "acme", "skill:solo", "use", True), ("u6", "acme", "skill:solo", "edit", False),
    ("u6", "acme", "skill:hq", "use", False),
    # 四档角色
    ("o", "acme", "workspace", "owner", True), ("o", "acme", "skill:anything", "owner", True),
    ("a", "acme", "workspace", "edit", True), ("a", "acme", "workspace", "owner", False),
    ("a", "acme", "tool:x", "owner", True), ("e", "acme", "skill:any", "use", True),
    ("e", "acme", "skill:any", "edit", False), ("e", "acme", "workspace", "edit", False),
    ("e", "acme", "model_group:m1", "use", True), ("v", "acme", "workspace", "use", True),
    ("v", "acme", "model_group:m1", "use", True), ("v", "acme", "model_group:m1", "edit", False),
    ("v", "acme", "skill:any", "use", False),
    # 跨空间隔离
    ("u3", "beta", "tool:crm", "use", False), ("u1", "beta", "workspace", "owner", True),
    ("o", "beta", "workspace", "use", False), ("a", "beta", "tool:x", "use", False),
    ("u2", "beta", "skill:rd", "use", False), ("u2", "beta", "workspace", "use", True),
    ("nobody", "acme", "workspace", "use", False),
]

AFTER_REVOKE = [
    ("u1", "acme", "skill:rd", "use", False), ("u2", "acme", "skill:rd", "edit", False),
    ("u4", "acme", "knowledge_base:law.contracts", "use", False), ("u7", "acme", "knowledge_base:law.contracts", "use", True),
    ("u1", "acme", "skill:hq", "use", False), ("u5", "acme", "tool:be", "owner", True),
    ("u3", "acme", "skill:hq", "use", True),
]


async def test_e5_fifty_cases(service):
    ids = await _scenario(service)
    assert len(CASES) + len(AFTER_REVOKE) == 50
    wrong = [case for case in CASES if service.check(*case[:4]) != case[4]]
    await service.revoke("a", "acme", f"dept:{ids['rd']}", "skill:rd")
    await service.remove_group_member("a", "acme", ids["legal"], "u4")
    await service.update_department("a", "acme", ids["rd"], parent_id=None)  # 研发中心脱离集团
    wrong += [case for case in AFTER_REVOKE if service.check(*case[:4]) != case[4]]
    log.info("E5 cases: %d total, %d wrong %s", len(CASES) + len(AFTER_REVOKE), len(wrong), wrong)
    assert wrong == []


async def _seed_large(access_db, service, *, policies: int, users: int, depts: int):
    """直接写库：三级部门树、每个用户挂一个部门、10000 条资源授权，然后整体重新加载。"""
    await service.create_workspace(workspace_id="big", name="Big", owner_user_id="root")
    dept_rows = [{"id": f"d{i}", "workspace_id": "big", "parent_id": None if i < 10 else f"d{i // 10 - 1}",
                  "name": f"部门{i}"} for i in range(depts)]
    rule_rows = [{"workspace_id": "big", "ptype": "g", "v0": f"dept:{r['id']}", "v1": f"dept:{r['parent_id']}",
                  "v2": "big", "v3": ""} for r in dept_rows if r["parent_id"]]
    rule_rows += [{"workspace_id": "big", "ptype": "g", "v0": f"user:u{i}", "v1": f"dept:d{depts - 1 - i % min(100, depts)}",
                   "v2": "big", "v3": ""} for i in range(users)]
    rule_rows += [{"workspace_id": "big", "ptype": "g", "v0": f"user:u{i}", "v1": "role:viewer", "v2": "big", "v3": ""}
                  for i in range(users)]
    kinds = ("skill", "tool", "knowledge_source")
    rule_rows += [{"workspace_id": "big", "ptype": "p", "v0": f"dept:d{i % depts}" if i % 3 else f"user:u{i % users}",
                   "v1": "big", "v2": f"{kinds[i % 3]}:r{i}", "v3": "use"} for i in range(policies)]
    engine = create_async_engine(access_db.admin_url)
    async with engine.begin() as conn:
        await conn.execute(insert(Department), sorted(dept_rows, key=lambda r: r["parent_id"] is not None))
        await conn.execute(insert(WorkspaceMember), [{"workspace_id": "big", "user_id": f"u{i}", "role": "viewer"}
                                                     for i in range(users)])
        await conn.execute(insert(DepartmentMember), [{"department_id": f"d{depts - 1 - i % min(100, depts)}", "user_id": f"u{i}",
                                                       "workspace_id": "big", "is_primary": True}
                                                      for i in range(users)])
        await conn.execute(insert(CasbinRule), rule_rows)
    await engine.dispose()
    started = time.perf_counter()
    assert await service.reload()
    return (time.perf_counter() - started) * 1000, len(rule_rows) + len(default_role_policies("big"))


def _timed(fn, args_list):
    samples = []
    for args in args_list:
        started = time.perf_counter()
        fn(*args)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return statistics.mean(samples), statistics.median(samples), samples[-1]


async def test_e5_ten_thousand_policies(access_db, service):
    reload_ms, total = await _seed_large(access_db, service, policies=10_000, users=1_000, depts=110)
    kinds = ("skill", "tool", "knowledge_source")
    checks = [(f"u{i % 1000}", "big", f"{kinds[i % 3]}:r{(i * 37) % 10_000}", "use") for i in range(2_000)]
    check_t = _timed(service.check, checks)
    visible = [(f"u{i}", "big", "skill", "use") for i in range(200)]
    visible_t = _timed(service.visible_resources, visible)
    sizes = [len(service.visible_resources(f"u{i}", "big", "skill", "use").ids) for i in range(20)]
    started = time.perf_counter()
    for i in range(20):
        await service.grant("root", "big", "user:u1", f"skill:new{i}", "use")
        await service.revoke("root", "big", "user:u1", f"skill:new{i}")
    write_ms = (time.perf_counter() - started) * 1000 / 40
    log.info("E5 perf: %d rules, reload %.0f ms; check mean/median/max %.3f/%.3f/%.3f ms; "
             "visible mean/median/max %.2f/%.2f/%.2f ms (set size ~%d); grant or revoke %.1f ms each",
             total, reload_ms, *check_t, *visible_t, statistics.mean(sizes), write_ms)
    check_mean, visible_mean = check_t[0], visible_t[0]
    assert service.check("u0", "big", "skill:r0", "use")  # p0 授给 user:u0
    assert check_mean < 5 and visible_mean < 100


async def test_single_scan_matches_official_implicit_permissions(access_db, service):
    from knowwork_server.access.engine import _implicit_permissions

    await _seed_large(access_db, service, policies=600, users=50, depts=30)
    enforcer = service.engine._enforcer
    for i in range(50):
        official = enforcer.get_implicit_permissions_for_user(f"user:u{i}", "big")
        assert sorted(map(tuple, _implicit_permissions(enforcer, f"user:u{i}", "big"))) == sorted(map(tuple, official))
