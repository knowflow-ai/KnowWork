"""给主线的装配函数。"""

from sqlalchemy.ext.asyncio import async_sessionmaker

from .bindings import ProfileBindings
from .clearance import AuthorizedToolRegistry, ToolClearance
from .facts import (
    ModelSource,
    NarrowingOf,
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
                   role_of: RoleOf | None = None, host_denied: tuple[str, ...] = (),
                   bindings: ProfileBindings | None = None, narrowing_of: NarrowingOf | None = None) -> ConversationProfiles:
    """sessionmaker 读写 kernel_binding 与 runtime_profile、查空间角色（每个事务自行声明工作空间）；
    role_of 可替换空间角色的查法（默认查 workspace_member）；bindings 传入时与检索工具共用同一份绑定；
    narrowing_of 是扩展给对话的范围收窄（conversations/scopes.py）。"""
    sources = ProfileSources(all_tools=registry.all_tools, clearance=clearance, skills=skills, models=models,
                             knowledge=knowledge_from_admin(knowledge),
                             role_of=role_of or member_role_lookup(sessionmaker), host_denied=host_denied,
                             narrowing_of=narrowing_of)
    return ConversationProfiles(ProfileCompiler(sources), bindings or ProfileBindings(sessionmaker))
