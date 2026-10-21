"""部门树与用户组：继承、移动、环、深度、删除与越权。"""

import pytest

from knowwork_server.access.errors import AccessDenied, Conflict, NotFound
from knowwork_server.access.org import MAX_DEPARTMENT_DEPTH
from knowwork_server.access.vocab import InvalidInput

pytestmark = pytest.mark.anyio


async def _chain(service, ws, depth):
    ids, parent = [], None
    for level in range(depth):
        parent = await service.create_department("alice", ws, f"部门{level}", parent)
        ids.append(parent)
    return ids


async def test_grant_to_parent_department_reaches_grandchild_members(service, ws):
    root, _mid, leaf = await _chain(service, ws, 3)
    await service.add_department_member("bob", ws, leaf, "dave")
    await service.grant("alice", ws, f"dept:{root}", "skill:s1", "use")
    assert service.check("dave", ws, "skill:s1", "use")
    assert not service.check("dave", ws, "skill:s1", "edit")
    await service.grant("alice", ws, f"dept:{leaf}", "skill:s2", "use")
    assert not service.check("carol", ws, "skill:s2", "use")


async def test_move_department_rewires_inheritance(service, ws):
    a, b = await _chain(service, ws, 2)
    c = await service.create_department("alice", ws, "独立")
    await service.add_department_member("alice", ws, b, "dave")
    await service.grant("alice", ws, f"dept:{a}", "tool:t1", "use")
    await service.grant("alice", ws, f"dept:{c}", "tool:t2", "use")
    assert service.check("dave", ws, "tool:t1", "use")
    await service.update_department("alice", ws, b, parent_id=c, name="改名")
    assert not service.check("dave", ws, "tool:t1", "use")
    assert service.check("dave", ws, "tool:t2", "use")
    await service.update_department("alice", ws, b, parent_id=None)
    assert not service.check("dave", ws, "tool:t2", "use")
    tree = await service.department_tree("dave", ws)
    assert sorted(n.name for n in tree) == ["改名", "独立", "部门0"]


async def test_move_rejects_cycles_and_depth(service, ws):
    a, _b, c = await _chain(service, ws, 3)
    with pytest.raises(Conflict):
        await service.update_department("alice", ws, a, parent_id=c)
    with pytest.raises(Conflict):
        await service.update_department("alice", ws, a, parent_id=a)
    deep = await _chain(service, ws, MAX_DEPARTMENT_DEPTH)
    with pytest.raises(Conflict):
        await service.create_department("alice", ws, "太深", deep[-1])
    with pytest.raises(Conflict):
        await service.update_department("alice", ws, a, parent_id=deep[-2])


async def test_delete_department(service, ws):
    a, b = await _chain(service, ws, 2)
    with pytest.raises(Conflict):
        await service.delete_department("alice", ws, a)
    await service.add_department_member("alice", ws, b, "dave")
    await service.grant("alice", ws, f"dept:{b}", "skill:s1", "use")
    await service.delete_department("alice", ws, b)
    assert not service.check("dave", ws, "skill:s1", "use")
    await service.delete_department("alice", ws, a)
    assert await service.department_tree("alice", ws) == ()
    with pytest.raises(NotFound):
        await service.delete_department("alice", ws, a)


async def test_department_member_rules(service, ws):
    a, b = await _chain(service, ws, 2)
    with pytest.raises(Conflict):
        await service.add_department_member("alice", ws, a, "outsider")
    await service.add_department_member("alice", ws, a, "dave", is_primary=True)
    await service.add_department_member("alice", ws, b, "dave", is_primary=True)
    await service.add_department_member("alice", ws, b, "dave", is_primary=True)
    tree = await service.department_tree("alice", ws)
    assert tree[0].members == (("dave", False),)
    assert tree[0].children[0].members == (("dave", True),)
    await service.remove_department_member("alice", ws, b, "dave")
    with pytest.raises(NotFound):
        await service.remove_department_member("alice", ws, b, "dave")
    with pytest.raises(NotFound):
        await service.add_department_member("alice", ws, "nope", "dave")


async def test_departments_of_other_workspace_are_invisible(service, ws):
    await service.create_workspace(workspace_id="ws2", name="二", owner_user_id="zed")
    foreign = await service.create_department("zed", "ws2", "外部门")
    with pytest.raises(NotFound):
        await service.create_department("alice", ws, "挂靠", foreign)
    with pytest.raises(NotFound):
        await service.grant("alice", ws, f"dept:{foreign}", "skill:s1", "use")
    with pytest.raises(NotFound):
        await service.delete_department("alice", ws, foreign)


@pytest.mark.parametrize("actor", ["carol", "dave", "stranger"])
async def test_org_writes_need_admin(service, ws, actor):
    with pytest.raises(AccessDenied):
        await service.create_department(actor, ws, "x")
    with pytest.raises(AccessDenied):
        await service.create_group(actor, ws, "x")


async def test_groups_lifecycle(service, ws):
    group = await service.create_group("bob", ws, "法务")
    with pytest.raises(Conflict):
        await service.create_group("bob", ws, "法务")
    other = await service.create_group("bob", ws, "客服")
    with pytest.raises(Conflict):
        await service.rename_group("bob", ws, other, "法务")
    await service.rename_group("bob", ws, other, "售后")
    await service.add_group_member("bob", ws, group, "dave")
    await service.add_group_member("bob", ws, group, "dave")
    with pytest.raises(Conflict):
        await service.add_group_member("bob", ws, group, "outsider")
    await service.grant("bob", ws, f"group:{group}", "knowledge_base:k1.ds1", "use")
    assert service.check("dave", ws, "knowledge_base:k1.ds1", "use")
    await service.remove_group_member("bob", ws, group, "dave")
    assert not service.check("dave", ws, "knowledge_base:k1.ds1", "use")
    with pytest.raises(NotFound):
        await service.remove_group_member("bob", ws, group, "dave")
    await service.add_group_member("bob", ws, group, "dave")
    await service.delete_group("bob", ws, group)
    assert not service.check("dave", ws, "knowledge_base:k1.ds1", "use")
    assert [(g.name, g.members) for g in await service.list_groups("dave", ws)] == [("售后", ())]
    with pytest.raises(NotFound):
        await service.delete_group("bob", ws, group)
    with pytest.raises(InvalidInput):
        await service.create_group("bob", ws, "   ")
