"""系统管理员：能直接进入任何空间，在任何空间里都按所有者处理，
不出现在成员列表里；其他人照旧按空间角色与授权判定。"""

import pytest

from knowwork_server.access.vocab import WORKSPACE_RESOURCE

pytestmark = pytest.mark.anyio


async def test_system_admin_counts_as_owner_in_any_workspace(service, ws):
    await service.create_workspace(workspace_id="ws2", name="空间二", owner_user_id="erin")
    assert not service.check("root", ws, WORKSPACE_RESOURCE, "use")
    service.set_system_admin("root")
    for workspace_id in (ws, "ws2"):
        assert service.check("root", workspace_id, WORKSPACE_RESOURCE, "owner")
        assert service.check("root", workspace_id, "knowledge_base:s1.kb1", "owner")
        assert service.visible_resources("root", workspace_id, "skill", "owner").all_
    assert service.is_system_admin("root") and not service.is_system_admin("alice")
    assert not service.check("carol", "ws2", WORKSPACE_RESOURCE, "use"), "别人照旧按空间成员身份判定"


async def test_system_admin_can_manage_members_without_being_one(service, ws):
    service.set_system_admin("root")
    await service.add_member("root", ws, "frank", "editor")
    members = {m.user_id for m in await service.list_members("root", ws)}
    assert "frank" in members and "root" not in members, "系统管理员不出现在成员列表里"


async def test_clearing_the_system_admin_takes_the_access_away(service, ws):
    service.set_system_admin("root")
    service.set_system_admin(None)
    assert not service.check("root", ws, WORKSPACE_RESOURCE, "use")
    assert not service.is_system_admin("root")
