"""扩展登记的资源类型（平铺、没有上级）：登记前不认；登记后能授权、判定、在调用方事务里给默认的「给谁用」、删除时清掉。
不能和内置类型、资源层级的类型重名。"""

import pytest

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
