"""工具使用权：内置工具对成员开放、其余工具要 tool 授权、执行前按当前授权重判，判定出错一律拒绝。"""

import pytest

from knowwork_server.profile.clearance import AuthorizedToolRegistry, ToolClearance
from knowwork_server.tools.registry import ToolContext, ToolFailure

from .profile_support import WS, Grants, registry

pytestmark = pytest.mark.anyio


def setup(members=None):
    grants = Grants(members or {"owner1": "owner", "admin1": "admin", "ed": "editor", "view": "viewer"})
    clearance = ToolClearance(grants, builtin=frozenset({"kw_now"}))
    return grants, clearance, AuthorizedToolRegistry(registry(), clearance)


def ctx(user):
    return ToolContext(workspace_id=WS, session_id="c_1", user_id=user)


async def names(reg, user):
    return sorted(s.name for s in await reg.available(ctx(user)))


async def test_builtin_tools_open_to_every_member_others_need_grant():
    grants, _, reg = setup()
    assert await names(reg, "ed") == ["kw_now"]
    assert await names(reg, "view") == ["kw_now"]
    assert await names(reg, "admin1") == ["crm_write", "erp_read", "kw_now"], "admin 默认有 tool:*"
    assert await names(reg, "owner1") == ["crm_write", "erp_read", "kw_now"]
    grants.grant("ed", "tool:erp_read")
    assert await names(reg, "ed") == ["erp_read", "kw_now"]


async def test_non_member_sees_nothing_even_builtin():
    _, clearance, reg = setup()
    assert await names(reg, "stranger") == []
    assert clearance.permits("stranger", WS, "kw_now") is False


async def test_invoke_rechecks_current_grant():
    grants, _, reg = setup()
    grants.grant("ed", "tool:crm_write")
    assert await reg.invoke(ctx("ed"), "crm_write", {"q": "x"}) == "done:x"
    grants.revoke("ed", "tool:crm_write")
    with pytest.raises(ToolFailure, match="已不在本会话的可用范围内"):
        await reg.invoke(ctx("ed"), "crm_write", {"q": "x"})


async def test_all_tools_is_unfiltered():
    _, _, reg = setup()
    assert sorted(s.name for s in await reg.all_tools(ctx("stranger"))) == ["crm_write", "erp_read", "kw_now"]


class Exploding:
    def check(self, *args):
        raise RuntimeError("casbin down")


async def test_checker_error_denies():
    clearance = ToolClearance(Exploding(), builtin=frozenset({"kw_now"}))
    assert clearance.permits("ed", WS, "kw_now") is False
    assert clearance.permitted("ed", WS, ["kw_now", "crm_write"]) == frozenset()


async def test_unhealthy_engine_denies():
    grants, clearance, _ = setup()
    grants.engine.invalidate()
    assert clearance.permits("admin1", WS, "kw_now") is False
