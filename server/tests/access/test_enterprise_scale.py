"""规模：1 万人、1000 个部门、5 个空间（其中 3 个按成员规则把全公司拉进来），第一次同步、整体重载、判定的耗时。

物化以后每个全员空间约 2.1 万条规则（成员角色 1 万、企业部门 1 万、部门上下级 1000），三个就是 6 万多条。
本机实测（2026-10-31）：第一次同步 9.4 秒、整体重载 0.8 秒、什么都没变的再同步 1.2 秒、一次判定 0.06 毫秒。
阈值放宽到约 5 倍，CI 慢一些也不误报；真变慢一个数量级会拦住（成员规则逐人加时第一次同步要 258 秒）。
"""

import time

import pytest

from knowwork_server.access.enterprise import OrgDepartmentInput as Dept
from knowwork_server.access.enterprise import OrgMembershipInput as Member

pytestmark = pytest.mark.anyio
PEOPLE, BRANCHES = 10_000, 10  # 集团 → 10 个事业部 → 各 10 个中心 → 各 9 个组：1 + 10 + 100 + 900 ≈ 1000 个部门


def _directory():
    departments = [Dept("root", None, "集团")]
    leaves = []
    for b in range(BRANCHES):
        departments.append(Dept(f"b{b}", "root", f"事业部{b}"))
        for c in range(BRANCHES):
            departments.append(Dept(f"b{b}c{c}", f"b{b}", f"中心{b}-{c}"))
            for t in range(9):
                leaves.append(f"b{b}c{c}t{t}")
                departments.append(Dept(leaves[-1], f"b{b}c{c}", f"组{b}-{c}-{t}"))
    members = [Member(f"u{i}", leaves[i % len(leaves)], True) for i in range(PEOPLE)]
    return departments, members


async def test_ten_thousand_people(service):
    for index in range(5):
        await service.create_workspace(workspace_id=f"w{index}", name=f"空间{index}", owner_user_id="boss")
    departments, members = _directory()
    await service.replace_org("ad", departments[:1], ())  # 先有根部门，才能建规则
    root = (await service.org_departments("boss", "w0"))[0].id
    for index in range(3):
        await service.add_member_rule("boss", f"w{index}", root, include_children=True, role="editor")

    started = time.perf_counter()
    change = await service.replace_org("ad", departments, members)
    synced = time.perf_counter() - started
    assert change.added == len(departments) - 1 and change.memberships_added == PEOPLE
    assert len(await service.list_members("boss", "w0")) == PEOPLE + 1

    started = time.perf_counter()
    assert await service.reload()
    reloaded = time.perf_counter() - started

    await service.grant("boss", "w0", f"org:{root}", "skill:all", "use")
    started = time.perf_counter()
    for i in range(0, PEOPLE, 100):
        assert service.check(f"u{i}", "w0", "skill:all", "use")
    checked = (time.perf_counter() - started) / (PEOPLE // 100)
    assert not service.check("u1", "w3", "skill:all", "use")  # 没有规则的空间：不是成员

    # 再同步一次、什么都没变：只比对不写
    started = time.perf_counter()
    again = await service.replace_org("ad", departments, members)
    unchanged = time.perf_counter() - started
    assert again.memberships_added == again.memberships_removed == 0

    assert synced < 50 and reloaded < 5 and unchanged < 10 and checked < 0.001, (synced, reloaded, unchanged, checked)
