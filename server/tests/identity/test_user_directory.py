"""用户目录：按 id 批量取资料（给成员列表补姓名邮箱）、按邮箱或姓名搜索（加成员时用，只给管理员）。"""

import pytest

from knowwork_server.identity.users import MAX_LOOKUP_IDS, MAX_SEARCH_LIMIT

from .conftest import PASSWORD, login

pytestmark = pytest.mark.anyio


@pytest.fixture
async def people(identity, alice):
    bob = await identity.users.create_user(email="bob@example.com", name="王小波", password=PASSWORD)
    carol = await identity.users.create_user(email="carol@corp.cn", name="Carol 李", password=PASSWORD)
    mallory = await identity.users.create_user(email="mallory_x@example.com", name="Mallory", password=PASSWORD)
    await identity.users.set_disabled(mallory.id, True)
    return {"alice": alice, "bob": bob, "carol": carol, "mallory": mallory}


async def test_get_many_returns_known_ids_only(identity, people):
    ids = (people["alice"].id, people["bob"].id, "missing", people["alice"].id)
    found = await identity.users.get_many(ids)
    assert set(found) == {people["alice"].id, people["bob"].id}
    assert found[people["bob"].id].email == "bob@example.com"
    assert await identity.users.get_many(()) == {}


async def test_get_many_splits_large_batches(identity, people):
    ids = (*(f"u{i}" for i in range(MAX_LOOKUP_IDS)), people["carol"].id)
    assert set(await identity.users.get_many(ids)) == {people["carol"].id}


async def test_search_matches_email_or_name_and_skips_disabled(identity, people):
    assert [u.name for u in await identity.users.search("example.com")] == ["Alice", "王小波"]
    assert [u.email for u in await identity.users.search("小波")] == ["bob@example.com"]
    assert [u.email for u in await identity.users.search("CAROL")] == ["carol@corp.cn"]
    assert await identity.users.search("mallory") == ()


async def test_search_treats_wildcards_literally(identity, people):
    # 下划线与百分号是 LIKE 通配符：必须按字面匹配，不能把「_」当任意字符
    assert await identity.users.search("y_x") == ()
    assert await identity.users.search("%") == ()


async def test_search_limits(identity, people):
    assert len(await identity.users.search("example", limit=1)) == 1
    assert await identity.users.search("   ") == ()
    with pytest.raises(ValueError):
        await identity.users.search("a", limit=MAX_SEARCH_LIMIT + 1)


async def test_search_endpoint_requires_admin(client, people, admins):
    assert (await client.get("/api/workspaces/ws1/users", params={"q": "bob"})).status_code == 401
    await login(client)
    assert (await client.get("/api/workspaces/ws1/users", params={"q": "bob"})).status_code == 403
    admins.add(people["alice"].id)
    response = await client.get("/api/workspaces/ws1/users", params={"q": "bob"})
    assert response.status_code == 200
    assert response.json() == [{"id": people["bob"].id, "email": "bob@example.com", "name": "王小波"}]


@pytest.mark.parametrize("params", [{}, {"q": ""}, {"q": "x" * 101}, {"q": "a", "limit": 0},
                                    {"q": "a", "limit": MAX_SEARCH_LIMIT + 1}])
async def test_search_endpoint_validates_input(client, people, admins, params):
    admins.add(people["alice"].id)
    await login(client)
    assert (await client.get("/api/workspaces/ws1/users", params=params)).status_code == 422


async def test_search_endpoint_rejects_api_keys(client, people, admins):
    admins.add(people["alice"].id)
    await login(client)
    issued = (await client.post("/api/auth/api-keys", json={"name": "k", "scopes": ["workspace:manage"],
                                                                  "workspaceId": "ws1"})).json()
    client.cookies.clear()
    response = await client.get("/api/workspaces/ws1/users", params={"q": "bob"},
                                headers={"Authorization": f"Bearer {issued['key']}"})
    assert response.status_code == 403
