"""空间与成员：四档角色、所有者保护、越权与跨空间。"""

import pytest

from knowwork_server.access.errors import AccessDenied, Conflict, NotFound
from knowwork_server.access.members import DEFAULT_WORKSPACE_ID
from knowwork_server.access.vocab import InvalidInput

pytestmark = pytest.mark.anyio


async def test_owner_and_admin_manage_members(service, ws):
    await service.add_member("bob", ws, "erin", "viewer")
    members = {m.user_id: m.role for m in await service.list_members("dave", ws)}
    assert members == {"alice": "owner", "bob": "admin", "carol": "editor", "dave": "viewer", "erin": "viewer"}
    assert service.check("erin", ws, "workspace", "use")
    assert not service.check("erin", ws, "workspace", "edit")


@pytest.mark.parametrize("actor", ["carol", "dave", "stranger"])
async def test_editor_viewer_and_strangers_cannot_manage(service, ws, actor):
    with pytest.raises(AccessDenied):
        await service.add_member(actor, ws, "erin", "viewer")
    with pytest.raises(AccessDenied):
        await service.remove_member(actor, ws, "dave")


async def test_stranger_cannot_read_members(service, ws):
    with pytest.raises(AccessDenied):
        await service.list_members("stranger", ws)


async def test_only_owner_touches_owner_role(service, ws):
    with pytest.raises(AccessDenied):
        await service.add_member("bob", ws, "erin", "owner")
    with pytest.raises(AccessDenied):
        await service.set_member_role("bob", ws, "alice", "viewer")
    with pytest.raises(AccessDenied):
        await service.remove_member("bob", ws, "alice")
    await service.set_member_role("alice", ws, "bob", "owner")
    assert service.check("bob", ws, "workspace", "owner")


async def test_last_owner_is_protected(service, ws):
    with pytest.raises(Conflict):
        await service.set_member_role("alice", ws, "alice", "admin")
    with pytest.raises(Conflict):
        await service.remove_member("alice", ws, "alice")
    await service.set_member_role("alice", ws, "bob", "owner")
    await service.set_member_role("alice", ws, "alice", "admin")
    assert not service.check("alice", ws, "workspace", "owner")
    assert service.check("alice", ws, "workspace", "edit")


async def test_role_change_takes_effect_immediately(service, ws):
    assert not service.check("dave", ws, "workspace", "edit")
    await service.set_member_role("alice", ws, "dave", "admin")
    assert service.check("dave", ws, "workspace", "edit")
    assert (await service.set_member_role("alice", ws, "dave", "admin")).role == "admin"
    await service.set_member_role("alice", ws, "dave", "viewer")
    assert not service.check("dave", ws, "workspace", "edit")


async def test_duplicate_and_missing_members(service, ws):
    with pytest.raises(Conflict):
        await service.add_member("alice", ws, "bob", "viewer")
    with pytest.raises(NotFound):
        await service.set_member_role("alice", ws, "nobody", "viewer")
    with pytest.raises(NotFound):
        await service.remove_member("alice", ws, "nobody")
    with pytest.raises(InvalidInput):
        await service.add_member("alice", ws, "erin", "superuser")
    with pytest.raises(InvalidInput):
        await service.add_member("alice", ws, "bad id!", "viewer")


async def test_removing_member_clears_groups_departments_and_direct_grants(service, ws):
    group = await service.create_group("alice", ws, "法务")
    dept = await service.create_department("alice", ws, "总部")
    await service.add_group_member("alice", ws, group, "dave")
    await service.add_department_member("alice", ws, dept, "dave", is_primary=True)
    await service.grant("alice", ws, "user:dave", "skill:s1", "use")
    await service.grant("alice", ws, f"group:{group}", "skill:s2", "use")
    await service.remove_member("alice", ws, "dave")
    assert not service.check("dave", ws, "skill:s1", "use")
    assert not service.check("dave", ws, "skill:s2", "use")
    assert [g.members for g in await service.list_groups("alice", ws)] == [()]
    assert (await service.department_tree("alice", ws))[0].members == ()
    await service.add_member("alice", ws, "dave", "viewer")
    assert not service.check("dave", ws, "skill:s1", "use")  # 重新加入不会恢复旧授权


async def test_cross_workspace_isolation(service, ws):
    await service.create_workspace(workspace_id="ws2", name="空间二", owner_user_id="zed")
    assert not service.check("alice", "ws2", "workspace", "use")
    assert not service.check("zed", ws, "workspace", "use")
    with pytest.raises(AccessDenied):
        await service.list_members("alice", "ws2")
    with pytest.raises(AccessDenied):
        await service.add_member("zed", ws, "erin", "viewer")
    with pytest.raises(Conflict):
        await service.create_workspace(workspace_id="ws2", name="重名", owner_user_id="zed")


async def test_default_workspace_is_idempotent(service):
    assert await service.ensure_default_workspace("root") == DEFAULT_WORKSPACE_ID
    assert await service.ensure_default_workspace("someone-else") == DEFAULT_WORKSPACE_ID
    assert [m.user_id for m in await service.list_members("root", DEFAULT_WORKSPACE_ID)] == ["root"]


async def test_reload_restores_identical_decisions(service, ws):
    group = await service.create_group("alice", ws, "客服")
    await service.add_group_member("alice", ws, group, "dave")
    await service.grant("alice", ws, f"group:{group}", "tool:t1", "edit")
    before = service.check("dave", ws, "tool:t1", "edit")
    assert await service.reload()
    assert before and service.check("dave", ws, "tool:t1", "edit")
