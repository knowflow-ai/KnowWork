"""运行配置编译：按用户、空间角色、授权与本轮选择编出不可变快照，SHA-256 作版本号。

主线整合（不改本包以外的装配，见各函数文档）：
    clearance = ToolClearance(access, builtin=frozenset(t.spec.name for t in (KW_NOW, *knowledge.tools)))
    registry = AuthorizedToolRegistry(StaticToolRegistry.of([KW_NOW, *knowledge.tools]), clearance)
    tools = build_tool_service(..., registry=registry, ...)        # 列工具与执行前都按当前授权重判
    profiles = build_profiles(scoped, registry, clearance, skills=skills.delivery.catalog,
                              models=models.catalog, knowledge=knowledge.admin)
    service = ConversationService(..., profiles=profiles)
    # 建表 PROFILE_TABLES，对 PROFILE_RLS_TABLES 开行级安全

本包的 __init__ 不急着导入表：conversations 只需要 snapshot 与 errors，不应因此把表挂到元数据上。
"""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AuthorizedToolRegistry": ".clearance", "ToolClearance": ".clearance",
    "ProfileFacts": ".compiler", "KnowledgeEntry": ".compiler", "compile_profile": ".compiler",
    "ProfileUnavailable": ".errors", "RuntimeProfile": ".snapshot", "TurnSelection": ".snapshot",
    "PROFILE_TABLES": ".models", "PROFILE_RLS_TABLES": ".models", "ProfileBindings": ".bindings",
    "ProfileCompiler": ".facts", "ProfileSources": ".facts", "ConversationProfiles": ".runtime",
    "build_profiles": ".assembly",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module, __name__), name)
