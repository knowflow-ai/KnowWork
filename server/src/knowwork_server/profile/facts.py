"""收集编译输入并编译。只依赖各模块已有的「按调用者过滤」入口，不另写一套权限判定：

- 工具：注册表全部工具 + ToolClearance（口径见 clearance）；
- 技能：skills 包的会话技能清单（已发布、已启用、按 skill 的 use 权限过滤，撞名撞认领已跳过）；
- 知识：knowledge 包的可查知识库，按知识源分组（knowledge_base 的 use 授权 ∩ 知识源的开放范围）；
- 空间角色：workspace_member 表。

任何一类事实取不到都抛 ProfileUnavailable：配置不全就不打开会话（失败关闭），原因只写日志。
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.models import WorkspaceMember
from ..access.rls import set_workspace
from ..tools.registry import ToolContext, ToolSpec
from .clearance import ToolClearance
from .compiler import KnowledgeEntry, ProfileFacts, compile_profile
from .errors import ProfileUnavailable
from .snapshot import NO_SELECTION, RuntimeProfile, TurnSelection

log = logging.getLogger(__name__)

RoleOf = Callable[[str, str], Awaitable[str | None]]
AllTools = Callable[[ToolContext], Awaitable[list[ToolSpec]]]
# (user_id, workspace_id) → 这个用户可用的知识源
KnowledgeOf = Callable[[str, str], Awaitable[tuple[KnowledgeEntry, ...]]]


class SkillSource(Protocol):
    async def for_session(self, workspace_id: str, user_id: str) -> tuple[Any, ...]: ...


class ModelSource(Protocol):
    async def default_capabilities(self, workspace_id: str) -> frozenset[str] | None: ...


# 要模型能看图才有用的宿主官方工具。官方 dsh-tool-fs 的 read_image 对所有会话可见、执行时才因模型不收图而失败，
# 模型会先转图片再去调它，白做几步（官方的做法是由组合方用作用域收窄收掉，见 dsh-tool-fs README）。
# 默认模型没勾「看图」（或查不到默认模型）时进 denied_tools，宿主经官方 tools.restrict 收掉。
VISION_TOOLS: tuple[str, ...] = ("read_image",)


class SourceLister(Protocol):
    """knowledge.admin.SourceAdmin 满足它：只列调用者可查的知识源，带上可查的知识库范围。"""

    async def queryable(self, actor: str, workspace_id: str) -> list[Any]: ...


@dataclass(frozen=True)
class ProfileSources:
    all_tools: AllTools
    clearance: ToolClearance
    skills: SkillSource
    models: ModelSource
    knowledge: KnowledgeOf
    role_of: RoleOf
    # 宿主里的官方工具（不经工具桥）中这一部署一律收掉的，例如没开沙箱时的 bash、文件工具与 present
    host_denied: tuple[str, ...] = ()


class ProfileCompiler:
    def __init__(self, sources: ProfileSources) -> None:
        self._s = sources

    async def compile(self, *, workspace_id: str, user_id: str, session_id: str,
                      selection: TurnSelection = NO_SELECTION) -> RuntimeProfile:
        try:
            facts = await self._facts(workspace_id, user_id, session_id, selection)
        except ProfileUnavailable:
            raise
        except Exception as error:
            log.exception("runtime profile facts unavailable for session %s", session_id)
            raise ProfileUnavailable() from error
        return compile_profile(facts)

    async def _facts(self, workspace_id: str, user_id: str, session_id: str,
                     selection: TurnSelection) -> ProfileFacts:
        clearance = self._s.clearance
        specs = tuple(await self._s.all_tools(ToolContext(workspace_id=workspace_id, session_id=session_id,
                                                          user_id=user_id)))
        if not clearance.member(user_id, workspace_id):
            return ProfileFacts(workspace_id=workspace_id, user_id=user_id, role=None, member=False, tools=specs,
                                selection=selection, host_denied=self._s.host_denied)
        capabilities = await self._s.models.default_capabilities(workspace_id) or frozenset()
        host_denied = self._s.host_denied if "vision" in capabilities else (*self._s.host_denied, *VISION_TOOLS)
        return ProfileFacts(
            workspace_id=workspace_id, user_id=user_id, role=await self._s.role_of(user_id, workspace_id),
            member=True, tools=specs, permitted_tools=clearance.permitted(user_id, workspace_id, [s.name for s in specs]),
            builtin_tools=clearance.builtin, skills=tuple(await self._s.skills.for_session(workspace_id, user_id)),
            knowledge=await self._s.knowledge(user_id, workspace_id), selection=selection,
            host_denied=host_denied)


def knowledge_from_admin(admin: SourceLister) -> KnowledgeOf:
    async def knowledge_of(user_id: str, workspace_id: str) -> tuple[KnowledgeEntry, ...]:
        return tuple(KnowledgeEntry(id=s.id, name=s.name, kind=s.kind, enabled=s.enabled, bases=tuple(s.bases))
                     for s in await admin.queryable(user_id, workspace_id))

    return knowledge_of


def member_role_lookup(sessionmaker: async_sessionmaker) -> RoleOf:
    """查 workspace_member 的空间角色。表开了行级安全，每个事务先声明工作空间。"""

    async def role_of(user_id: str, workspace_id: str) -> str | None:
        async with sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            return await session.scalar(select(WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id))

    return role_of
