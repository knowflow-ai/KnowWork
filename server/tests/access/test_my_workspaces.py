"""「我能进的空间」：只列有成员身份的空间并带上角色；系统管理员列全部、都按所有者；绑定空间的 API Key 只看到那一个。"""

import httpx
import pytest
from fastapi import FastAPI, Header

from knowwork_server.access.api import build_router
from knowwork_server.auth.principal import Principal

pytestmark = pytest.mark.anyio


async def test_lists_workspaces_with_the_callers_role(service, ws):
    await service.create_workspace(workspace_id="ws2", name="空间二", owner_user_id="bob")
    assert [(w.id, w.role) for w in await service.my_workspaces("alice")] == [("ws1", "owner")]
    assert [(w.id, w.role) for w in await service.my_workspaces("bob")] == [("ws1", "admin"), ("ws2", "owner")]
    assert [(w.id, w.name, w.role) for w in await service.my_workspaces("carol")] == [("ws1", "空间一", "editor")]
    assert await service.my_workspaces("nobody") == ()
    service.set_system_admin("root")
    assert [(w.id, w.role) for w in await service.my_workspaces("root")] == [("ws1", "owner"), ("ws2", "owner")]


async def test_a_bound_api_key_sees_only_its_workspace(service, ws):
    await service.create_workspace(workspace_id="ws2", name="空间二", owner_user_id="bob")

    async def principal(x_key: str | None = Header(default=None)) -> Principal:
        if x_key:
            return Principal(user_id="bob", auth_method="api_key", api_key_id="k1", workspace_id=x_key)
        return Principal(user_id="bob", auth_method="session")

    app = FastAPI()
    app.include_router(build_router(service, principal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        assert [w["id"] for w in (await http.get("/api/workspaces")).json()] == ["ws1", "ws2"]
        assert [w["id"] for w in (await http.get("/api/workspaces", headers={"x-key": "ws2"})).json()] == ["ws2"]
