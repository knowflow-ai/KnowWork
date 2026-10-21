"""运行配置测试的公共替身：真实 Casbin 判定器（内存）与几个固定的事实来源。"""

from dataclasses import dataclass

from knowwork_server.access.engine import PolicyEngine
from knowwork_server.access.rules import Rule, default_role_policies, member_role, permission, user_key
from knowwork_server.skills.views import CatalogEntry
from knowwork_server.tools.registry import BuiltinTool, StaticToolRegistry, ToolSpec

WS = "default"
SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"], "additionalProperties": False}


async def _echo(_ctx, arguments):
    return f"done:{arguments['q']}"


BUILTIN = BuiltinTool(ToolSpec("kw_now", "现在几点", {"type": "object", "properties": {}}, "read"), _echo)
CRM = BuiltinTool(ToolSpec("crm_write", "写客户台账", SCHEMA, "write"), _echo)
ERP = BuiltinTool(ToolSpec("erp_read", "查订单", SCHEMA, "read"), _echo)


def registry() -> StaticToolRegistry:
    return StaticToolRegistry.of([BUILTIN, CRM, ERP])


class Grants:
    """可变的授权集合 + 真实的 Casbin 判定器；改授权后整体重载，与生产的回放效果一致。"""

    def __init__(self, members: dict[str, str]) -> None:
        self.members = dict(members)
        self.extra: set[Rule] = set()
        self.engine = PolicyEngine()
        self.reload()

    def reload(self) -> None:
        rules = [*default_role_policies(WS), *(member_role(u, r, WS) for u, r in self.members.items()), *self.extra]
        self.engine.replace(rules)

    def grant(self, user: str, resource: str, action: str = "use") -> None:
        self.extra = {*self.extra, permission(user_key(user), WS, resource, action)}
        self.reload()

    def revoke(self, user: str, resource: str, action: str = "use") -> None:
        self.extra = self.extra - {permission(user_key(user), WS, resource, action)}
        self.reload()

    def check(self, user_id, workspace_id, resource, action):
        return self.engine.check(user_id, workspace_id, resource, action)

    def visible_resources(self, user_id, workspace_id, resource_type, action):
        return self.engine.visible(user_id, workspace_id, resource_type, action)


def skill(name: str, version: str = "1.0.0", digest: str = "a" * 64, description: str = "技能说明") -> CatalogEntry:
    return CatalogEntry(skill_id=f"s_{name}", release_id=f"r_{name}", scope="workspace", name=name,
                        description=description, version=version, digest=digest, content_digest="c" * 64,
                        allowed_tools=(), completion_tools=(), unlocks=(), model_invocable=True, user_invocable=True)


@dataclass(frozen=True)
class Model:
    id: str
    alias: str
    name: str


async def ensure_profile_tables(sessionmaker):
    """延迟导入并建 kernel_binding、runtime_profile。

    收集阶段就导入会把两张表挂到 Base.metadata 上，迁移合同测试（test_migrations）会因主线还没写迁移而失败；
    本组测试文件名排在 test_migrations 之后，到运行时才导入。主线补上迁移并登记 MODEL_MODULES 后可改回顶部导入。
    """
    from knowwork_server.db.base import Base
    from knowwork_server.profile.models import PROFILE_TABLES

    engine = sessionmaker.kw["bind"]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=list(PROFILE_TABLES)))
    return sessionmaker
