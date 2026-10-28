"""空间本身：系统管理员新建并指定所有者、列出全部；所有者或系统管理员改名；系统管理员关闭（软删除），
关闭后谁都进不去（系统管理员也不行）、重载后照样关着；默认空间不能关。"""

import pytest

from knowwork_server.access.errors import AccessDenied, Conflict, NotFound
from knowwork_server.access.service import AccessService
from knowwork_server.access.vocab import WORKSPACE_RESOURCE
from knowwork_server.access.workspaces import DEFAULT_WORKSPACE_ID

pytestmark = pytest.mark.anyio


async def test_system_admin_opens_a_workspace_for_an_owner(service, ws):
    with pytest.raises(AccessDenied):
        await service.open_workspace("alice", name="财务部", owner_user_id="erin")
    service.set_system_admin("root")
    new = await service.open_workspace("root", name="财务部", owner_user_id="erin")
    assert new.startswith("ws_")
    assert service.check("erin", new, WORKSPACE_RESOURCE, "owner")
    assert not service.check("alice", new, WORKSPACE_RESOURCE, "use"), "别的空间的所有者进不去"
    listed = {w.id: w for w in await service.all_workspaces("root")}
    assert listed[new].name == "财务部" and listed[new].owners == ("erin",)
    assert listed[ws].owners == ("alice",)
    with pytest.raises(AccessDenied):
        await service.all_workspaces("alice")


async def test_owner_or_system_admin_renames(service, ws):
    await service.rename_workspace("alice", ws, "  研发中心 ")
    assert [w.name for w in await service.my_workspaces("alice")] == ["研发中心"]
    with pytest.raises(AccessDenied):
        await service.rename_workspace("bob", ws, "x")  # 管理员不行，要所有者
    service.set_system_admin("root")
    await service.rename_workspace("root", ws, "研发")
    assert [w.name for w in await service.my_workspaces("carol")] == ["研发"]


async def test_closing_locks_everyone_out_and_survives_reload(service, ws):
    service.set_system_admin("root")
    with pytest.raises(AccessDenied):
        await service.close_workspace("alice", ws)
    await service.close_workspace("root", ws)
    for user in ("alice", "carol", "root"):
        assert not service.check(user, ws, WORKSPACE_RESOURCE, "use")
        assert await service.my_workspaces(user) == ()
    assert not service.visible_resources("root", ws, "skill", "use").all_
    with pytest.raises(AccessDenied):
        await service.add_member("root", ws, "frank", "editor")
    with pytest.raises(NotFound):
        await service.close_workspace("root", ws)
    fresh = AccessService(service._sessionmaker)
    assert await fresh.reload()
    assert not fresh.check("alice", ws, WORKSPACE_RESOURCE, "use"), "重启以后照样关着"


async def test_default_workspace_cannot_be_closed(service):
    await service.ensure_default_workspace("alice")
    service.set_system_admin("root")
    with pytest.raises(Conflict):
        await service.close_workspace("root", DEFAULT_WORKSPACE_ID)
