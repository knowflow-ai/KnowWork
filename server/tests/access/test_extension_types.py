"""扩展登记的资源类型（平铺、没有上级）：登记前不认；登记后能授权、判定、在调用方事务里给默认的「给谁用」、删除时清掉。
不能和内置类型、资源层级的类型重名。"""

import pytest

from knowwork_server.access.errors import NotFound
from knowwork_server.access.rls import set_workspace
from knowwork_server.access.vocab import InvalidInput
from knowwork_server.db.base import make_engine, make_sessionmaker

pytestmark = pytest.mark.anyio


async def test_extension_types_are_grantable_once_registered(service, ws):
    with pytest.raises(InvalidInput):
        await service.grant("alice", ws, "user:dave", "data_source:s1", "use")
    service.add_resource_types(frozenset({"data_source"}))
    await service.grant("alice", ws, "user:dave", "data_source:s1", "edit")
    assert service.check("dave", ws, "data_source:s1", "use") and service.check("dave", ws, "data_source:s1", "edit")
    assert not service.check("dave", ws, "data_source:s1", "owner")
    assert not service.check("carol", ws, "data_source:s1", "use"), "资源都靠授权"
    assert service.visible_resources("dave", ws, "data_source", "use").ids == {"s1"}


async def test_seed_and_purge_in_the_callers_transaction(service, ws, access_db):
    service.add_resource_types(frozenset({"data_source"}))
    engine = make_engine(access_db.app_url)
    sessionmaker = make_sessionmaker(engine)
    async with sessionmaker() as session, session.begin():
        await set_workspace(session, ws)
        change = await service.seed_users(session, ws, "data_source:s1", ("role:editor",))
    await service.settle(change)
    assert service.check("carol", ws, "data_source:s1", "use"), "新建时默认给全体成员"
    async with sessionmaker() as session, session.begin():
        await set_workspace(session, ws)
        change = await service.purge_grants(session, ws, resources=("data_source:s1",))
    await service.settle(change)
    assert not service.check("carol", ws, "data_source:s1", "use")
    await engine.dispose()


async def test_extension_types_cannot_shadow_others(service):
    for bad in ("knowledge_base", "workspace", "Bad-Type"):
        with pytest.raises(ValueError):
            service.add_resource_types(frozenset({bad}))


async def test_owners_share_their_own_extension_resources(service, ws):
    """所有者分享（例如问数的看板）：谁算所有者由扩展核对，这里不要求空间管理员；整份替换，档只能是使用、编辑。"""
    service.add_resource_types(frozenset({"dashboard"}))
    shared = await service.set_shares(ws, "dashboard:d1", {"user:dave": "use", "role:editor": "edit"})
    assert {(g.subject, g.action) for g in shared} == {("user:dave", "use"), ("role:editor", "edit")}
    assert service.check("carol", ws, "dashboard:d1", "edit"), "全体成员能编辑"
    assert not service.check("carol", ws, "dashboard:d1", "owner"), "管理那一档分享不出去"
    await service.set_shares(ws, "dashboard:d1", {"user:dave": "edit"})
    assert {(g.subject, g.action) for g in await service.shares(ws, "dashboard:d1")} == {("user:dave", "edit")}
    assert not service.check("carol", ws, "dashboard:d1", "use"), "整份替换：没再列的就收回了"
    await service.set_shares(ws, "dashboard:d1", {})
    assert await service.shares(ws, "dashboard:d1") == ()


async def test_owner_sharing_leaves_admin_manage_grants_alone(service, ws):
    """管理员在授权页给的「管理」不在所有者手里：所有者整份替换分享时留着，名单里也不列。"""
    service.add_resource_types(frozenset({"dashboard"}))
    await service.grant("alice", ws, "user:dave", "dashboard:d1", "owner")
    await service.set_shares(ws, "dashboard:d1", {"user:carol": "use", "user:dave": "use"})
    assert service.check("dave", ws, "dashboard:d1", "owner"), "管理还在"
    assert {(g.subject, g.action) for g in await service.shares(ws, "dashboard:d1")} == {("user:carol", "use")}, \
        "有管理的不再加一档：同一主体对同一资源只留一档"


async def test_sharing_only_reaches_extension_types_and_two_levels(service, ws):
    """内置的技能、工具、知识库仍然只有空间管理员能授；「管理」不能分享出去；对象要在这个空间里。"""
    service.add_resource_types(frozenset({"dashboard"}))
    for resource in ("skill:report", "knowledge_base:kb1.*"):
        with pytest.raises(InvalidInput):
            await service.set_shares(ws, resource, {"user:dave": "use"})
    with pytest.raises(InvalidInput):
        await service.set_shares(ws, "dashboard:d1", {"user:dave": "owner"})
    with pytest.raises(NotFound):
        await service.set_shares(ws, "dashboard:d1", {"user:nobody": "use"})
