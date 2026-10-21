"""给主线的装配函数。"""

from sqlalchemy.ext.asyncio import async_sessionmaker

from .bindings import ProfileBindings
from .clearance import AuthorizedToolRegistry, ToolClearance
from .facts import (
    ModelSource,
    ProfileCompiler,
    ProfileSources,
    RoleOf,
    SkillSource,
    SourceLister,
    knowledge_from_admin,
    member_role_lookup,
)
from .runtime import ConversationProfiles


def build_profiles(sessionmaker: async_sessionmaker, registry: AuthorizedToolRegistry, clearance: ToolClearance, *,
                   skills: SkillSource, models: ModelSource, knowledge: SourceLister,
                   role_of: RoleOf | None = None, host_denied: tuple[str, ...] = ()) -> ConversationProfiles:
    """sessionmaker 读写 kernel_binding 与 runtime_profile、查空间角色（每个事务自行声明工作空间）；
    role_of 可替换空间角色的查法（默认查 workspace_member）。"""
    sources = ProfileSources(all_tools=registry.all_tools, clearance=clearance, skills=skills, models=models,
                             knowledge=knowledge_from_admin(knowledge),
                             role_of=role_of or member_role_lookup(sessionmaker), host_denied=host_denied)
    return ConversationProfiles(ProfileCompiler(sources), ProfileBindings(sessionmaker))
