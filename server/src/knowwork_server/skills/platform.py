"""企业技能的管理（系统管理员）：给扩展的企业技能管理视图与接口用（ApiContext.platform_skills），开源这边没有入口。

- 上传、换版本、发布、启停、删除都走 SkillService 的 platform 范围（存在平台配置所在的空间名下）；
- 发给哪些空间：audience 是空间 id 列表，"*" 是全部空间（以后新建的也有）；刚建时为空，发出去之前哪个空间都看不到；
- 从空间技能「收为企业技能」：复制那个空间技能当前的发布版本（包按摘要引用同一份存储），第一次是新建企业技能，
  以后再收同一个空间技能是它的新草稿（origin_skill_id 记来源）；企业技能里已有同名、但不是从它来的就拦下。
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.rls import set_workspace
from ..access.workspaces import PLATFORM_WORKSPACE_ID
from .errors import PackageInvalid, SkillConflict, SkillForbidden, SkillNotFound
from .layers import ALL_WORKSPACES, published_description
from .models import Skill
from .package import PackageLimits
from .service import MAX_SKILLS_PER_SCOPE, SkillService, claimed_tools, ensure_unclaimed, lock_scope
from .views import Actor, ReleaseView, SkillView, skill_view

MAX_AUDIENCE = 500


@dataclass(frozen=True)
class Promotable:
    """可以收为企业技能的空间技能（已发布）。"""

    workspace_id: str
    workspace_name: str
    skill_id: str
    name: str
    description: str
    version: str

    def to_json(self) -> dict[str, Any]:
        return {"workspaceId": self.workspace_id, "workspaceName": self.workspace_name, "skillId": self.skill_id,
                "name": self.name, "description": self.description, "version": self.version}


class PlatformSkillAdmin:
    def __init__(self, sessionmaker: async_sessionmaker, access: Any, skills: SkillService) -> None:
        self._sessionmaker = sessionmaker
        self._access = access  # AccessService：判系统管理员、列出全部空间
        self._skills = skills

    @property
    def limits(self) -> PackageLimits:
        return self._skills.limits

    def _actor(self, user_id: str) -> Actor:
        if not self._access.is_system_admin(user_id):
            raise SkillForbidden("只有系统管理员可以管理企业技能")
        return Actor(user_id=user_id, workspace_id=PLATFORM_WORKSPACE_ID)

    @asynccontextmanager
    async def _tx(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    # ---- 照搬空间技能的那几个操作，范围是 platform ----

    async def skills(self, user_id: str) -> tuple[SkillView, ...]:
        return await self._skills.list(self._actor(user_id), "platform")

    async def get(self, user_id: str, skill_id: str) -> tuple[SkillView, tuple[ReleaseView, ...]]:
        return await self._skills.get(self._actor(user_id), skill_id, scope="platform")

    async def install(self, user_id: str, archive: bytes) -> SkillView:
        return await self._skills.install(self._actor(user_id), "platform", archive)

    async def replace_draft(self, user_id: str, skill_id: str, archive: bytes, *,
                            expected_revision: int | None = None) -> SkillView:
        return await self._skills.replace_draft(self._actor(user_id), skill_id, archive, scope="platform",
                                                expected_revision=expected_revision)

    async def publish(self, user_id: str, skill_id: str, *, version: str | None = None,
                      expected_revision: int | None = None) -> ReleaseView:
        return await self._skills.publish(self._actor(user_id), skill_id, scope="platform", version=version,
                                          expected_revision=expected_revision)

    async def set_enabled(self, user_id: str, skill_id: str, enabled: bool) -> SkillView:
        return await self._skills.set_enabled(self._actor(user_id), skill_id, enabled, scope="platform")

    async def delete(self, user_id: str, skill_id: str) -> None:
        await self._skills.delete(self._actor(user_id), skill_id, scope="platform")

    async def set_env(self, user_id: str, skill_id: str, changes: dict[str, str | None]) -> SkillView:
        return await self._skills.set_env(self._actor(user_id), skill_id, changes, scope="platform")

    # ---- 发给哪些空间 ----

    async def set_audience(self, user_id: str, skill_id: str, workspaces: list[str]) -> SkillView:
        self._actor(user_id)
        audience = await self._audience(user_id, workspaces)
        async with self._tx(PLATFORM_WORKSPACE_ID) as session:
            row = await self._platform_row(session, skill_id, lock=True)
            row.audience = audience
            await session.flush()
            await session.refresh(row)
            return skill_view(row)

    async def _audience(self, user_id: str, workspaces: list[str]) -> list[str]:
        if ALL_WORKSPACES in workspaces:
            return [ALL_WORKSPACES]
        if len(workspaces) > MAX_AUDIENCE:
            raise PackageInvalid("invalid_audience", f"最多选 {MAX_AUDIENCE} 个空间", field="workspaces")
        known = {summary.id for summary in await self._access.all_workspaces(user_id)}
        unknown = [workspace for workspace in workspaces if workspace not in known]
        if unknown:
            raise PackageInvalid("invalid_audience", f"空间 {unknown[0]} 不存在", field="workspaces")
        return sorted(set(workspaces))

    @staticmethod
    async def _platform_row(session: AsyncSession, skill_id: str, *, lock: bool = False) -> Skill:
        stmt = select(Skill).where(Skill.id == skill_id, Skill.workspace_id == PLATFORM_WORKSPACE_ID,
                                   Skill.scope_key == "platform")
        row = await session.scalar(stmt.with_for_update() if lock else stmt)
        if row is None:
            raise SkillNotFound()
        return row

    # ---- 从空间技能收为企业技能 ----

    async def promotable(self, user_id: str) -> tuple[Promotable, ...]:
        """全部空间里已发布的空间技能，按空间、名字排。"""
        self._actor(user_id)
        found: list[Promotable] = []
        for workspace in await self._access.all_workspaces(user_id):
            async with self._tx(workspace.id) as session:
                rows = (await session.scalars(select(Skill).where(
                    Skill.workspace_id == workspace.id, Skill.scope_key == "workspace",
                    Skill.status == "published").order_by(Skill.name))).all()
            found.extend(Promotable(workspace_id=workspace.id, workspace_name=workspace.name, skill_id=row.id,
                                    name=row.name, description=published_description(row),
                                    version=row.published_version or "")
                         for row in rows)
        return tuple(found)

    async def promote(self, user_id: str, workspace_id: str, skill_id: str) -> SkillView:
        actor = self._actor(user_id)
        async with self._tx(workspace_id) as session:
            source = await session.scalar(select(Skill).where(
                Skill.id == skill_id, Skill.workspace_id == workspace_id, Skill.scope_key == "workspace"))
            if source is None or source.status != "published" or not source.published_digest:
                raise SkillNotFound("空间技能不存在或还没发布")
            digest, manifest = source.published_digest, dict(source.published_manifest or {})
            name, description = source.name, source.description
        async with self._tx(PLATFORM_WORKSPACE_ID) as session:
            await lock_scope(session, PLATFORM_WORKSPACE_ID)
            target = await session.scalar(select(Skill).where(
                Skill.workspace_id == PLATFORM_WORKSPACE_ID, Skill.scope_key == "platform",
                Skill.origin_skill_id == skill_id).limit(1))
            ensure_unclaimed(manifest, name, await claimed_tools(session, PLATFORM_WORKSPACE_ID, {"platform"},
                                                                 target.id if target else None))
            if target is None:
                target = await self._new(session, actor, name, description, digest, manifest, skill_id)
            elif target.draft_digest == digest:
                raise SkillConflict("already_promoted", "企业技能里已经是这一版了")
            else:
                target.description, target.draft_digest, target.draft_manifest = description, digest, manifest
                target.draft_revision += 1
                target.has_unpublished_changes = digest != target.published_digest
            await session.flush()
            await session.refresh(target)
            return skill_view(target)

    @staticmethod
    async def _new(session: AsyncSession, actor: Actor, name: str, description: str, digest: str,
                   manifest: dict, origin: str) -> Skill:
        names = (await session.scalars(select(Skill.name).where(
            Skill.workspace_id == PLATFORM_WORKSPACE_ID, Skill.scope_key == "platform"))).all()
        if name in names:
            raise SkillConflict("skill_exists", f"企业技能里已经有同名技能「{name}」")
        if len(names) >= MAX_SKILLS_PER_SCOPE:
            raise SkillConflict("too_many_skills", f"同一范围最多 {MAX_SKILLS_PER_SCOPE} 个技能")
        row = Skill(id=uuid.uuid4().hex, workspace_id=PLATFORM_WORKSPACE_ID, scope="platform", scope_key="platform",
                    owner_id=actor.user_id, name=name, description=description, status="draft", enabled=True,
                    draft_digest=digest, draft_manifest=manifest, draft_revision=1, has_unpublished_changes=True,
                    origin_skill_id=origin, audience=[])
        session.add(row)
        return row
