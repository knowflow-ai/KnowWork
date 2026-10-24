"""技能的安装、草稿、发布、启停与删除。

状态机：
- 安装：新建技能，status = draft，draft_revision = 1，has_unpublished_changes = 真；
- 更新草稿：换一个包，draft_revision + 1；已发布的技能同时把 has_unpublished_changes 置真；
- 发布：把当前草稿冻结成一条不可变的 skill_release，status = published，has_unpublished_changes = 假；
  版本号取请求给的，或 frontmatter 的 version，都没有时取包摘要前 12 位；(skill_id, version) 重复即 409；
- 启停只影响是否进入运行配置；删除连同发布记录一起删（运行事实保留）。

范围与权限：
- 空间技能（scope = workspace）按 access 的 RBAC：安装要对全部技能有 owner（空间 owner、admin），
  读要 use，改草稿、启停要 edit，发布、删除要 owner；
- 个人技能（scope = personal）只有本人可见可用，且本人必须仍是空间成员；别人一律当作不存在。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Protocol

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.engine import VisibleSet
from ..access.rls import set_workspace
from ..access.rules import RuleChange
from .blobstore import BlobStore
from .claims import DEFAULT_BASIC_TOOLS, check_claims
from .errors import BlobIntegrityError, PackageInvalid, SkillConflict, SkillError, SkillForbidden, SkillNotFound
from .manifest import VERSION
from .models import Skill, SkillRelease
from .package import PackageLimits, ValidatedPackage, validate_skill_zip
from .views import SCOPES, Actor, ReleaseView, SkillView, release_view, skill_view

MAX_SKILLS_PER_SCOPE = 500


class AccessChecker(Protocol):
    """access.service.AccessService 满足这个协议。"""

    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...

    def visible_resources(self, user_id: str, workspace_id: str, resource_type: str, action: str) -> VisibleSet: ...

    async def purge_grants(self, session: AsyncSession, workspace_id: str, *, resources: tuple[str, ...] = (),
                           prefixes: tuple[str, ...] = ()) -> RuleChange: ...

    async def settle(self, change: RuleChange) -> None: ...


class StorageUnavailable(SkillError):
    status = 503

    def __init__(self) -> None:
        super().__init__("storage_unavailable", "技能存储暂时不可用，请稍后再试")


def scope_key(scope: str, user_id: str) -> str:
    return "workspace" if scope == "workspace" else f"user:{user_id}"


def manifest_json(package: ValidatedPackage, unlocks: tuple[str, ...]) -> dict[str, Any]:
    described = {**package.manifest.describe(), "contentDigest": package.content_digest, "unlocks": list(unlocks),
                 "rootPrefix": package.root_prefix,
                 "files": [{"path": f.path, "size": f.size, "sha256": f.sha256} for f in package.files]}
    if package.workflow is None:
        return described
    # 固定工作流随发布冻结进 skill_release.manifest，运行时从发布版本取，不再读包
    return {**described, "workflow": package.workflow.to_json(), "workflowDigest": package.workflow.digest}


class SkillService:
    def __init__(self, sessionmaker: async_sessionmaker, access: AccessChecker, blobs: BlobStore, *,
                 basic_tools: frozenset[str] = DEFAULT_BASIC_TOOLS, limits: PackageLimits | None = None,
                 max_concurrent_validations: int = 2) -> None:
        self._sessionmaker = sessionmaker
        self._access = access
        self._blobs = blobs
        self._basic = basic_tools
        self._limits = limits or PackageLimits()
        self._validating = asyncio.Semaphore(max_concurrent_validations)

    @property
    def limits(self) -> PackageLimits:
        return self._limits

    # ---- 事务与权限 ----

    @asynccontextmanager
    async def _tx(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    def _member(self, actor: Actor) -> None:
        if not self._access.check(actor.user_id, actor.workspace_id, "workspace", "use"):
            raise SkillForbidden()

    def _authorize(self, actor: Actor, row: Skill, action: str) -> None:
        """个人技能只有本人；空间技能看 RBAC。看不到的一律 404，有读权限但动作不够才是 403。"""
        self._member(actor)
        if row.scope == "personal":
            if row.owner_id != actor.user_id:
                raise SkillNotFound()
            return
        resource = f"skill:{row.id}"
        if not self._access.check(actor.user_id, actor.workspace_id, resource, "use"):
            raise SkillNotFound()
        if action != "use" and not self._access.check(actor.user_id, actor.workspace_id, resource, action):
            raise SkillForbidden()

    def _require_install(self, actor: Actor, scope: str) -> None:
        if scope not in SCOPES:
            raise PackageInvalid("invalid_scope", "scope 只能是 personal 或 workspace", field="scope")
        self._member(actor)
        if scope == "workspace" and not self._access.visible_resources(
                actor.user_id, actor.workspace_id, "skill", "owner").all_:
            raise SkillForbidden("只有空间管理员可以安装空间技能")

    async def _load(self, session: AsyncSession, actor: Actor, skill_id: str, scope: str | None, *,
                    lock: bool = False) -> Skill:
        """scope 不为空时只认该范围的技能：空间路由碰不到个人技能，反之亦然。"""
        stmt = select(Skill).where(Skill.id == skill_id, Skill.workspace_id == actor.workspace_id)
        row = await session.scalar(stmt.with_for_update() if lock else stmt)
        if row is None or (scope is not None and row.scope != scope):
            raise SkillNotFound()
        return row

    # ---- 包与认领 ----

    async def _store(self, package: ValidatedPackage) -> None:
        try:
            await self._blobs.put(package.archive)
            await self._blobs.put(package.manifest.body.encode())
        except BlobIntegrityError as error:
            raise StorageUnavailable() from error

    async def _claims_in_scope(self, session: AsyncSession, actor: Actor, key: str,
                               exclude_id: str | None) -> dict[str, str]:
        """同一范围（空间技能与该用户的个人技能视为同一个认领空间）里其他技能已认领的专业工具。"""
        keys = {"workspace", key}
        rows = (await session.scalars(select(Skill).where(Skill.workspace_id == actor.workspace_id,
                                                          Skill.scope_key.in_(keys)))).all()
        claimed: dict[str, str] = {}
        for row in rows:
            if row.id == exclude_id:
                continue
            for manifest in (row.draft_manifest, row.published_manifest or {}):
                for tool in manifest.get("unlocks", ()):
                    claimed.setdefault(tool, row.name)
        return claimed

    async def _validate(self, archive: bytes) -> ValidatedPackage:
        """解压与逐个算哈希是 CPU 活，放到线程里；同时校验的包数有上限，防止上传把服务拖死。"""
        async with self._validating:
            return await asyncio.to_thread(validate_skill_zip, archive, self._limits)

    @staticmethod
    async def _lock_scope(session: AsyncSession, workspace_id: str) -> None:
        """同一空间的安装、改草稿、发布串行：名称查重与工具认领检查不能被并发绕过。"""
        await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"skill:{workspace_id}"})

    # ---- 安装与草稿 ----

    async def install(self, actor: Actor, scope: str, archive: bytes) -> SkillView:
        self._require_install(actor, scope)
        package = await self._validate(archive)
        await self._store(package)  # 按摘要寻址、写入幂等：先于事务写入，不在持锁时上传
        key = scope_key(scope, actor.user_id)
        async with self._tx(actor.workspace_id) as session:
            await self._lock_scope(session, actor.workspace_id)
            await self._ensure_new(session, actor, key, package.manifest.name)
            unlocks = check_claims(package.manifest, self._basic, await self._claims_in_scope(session, actor, key, None))
            row = Skill(id=uuid.uuid4().hex, workspace_id=actor.workspace_id, scope=scope, scope_key=key,
                        owner_id=actor.user_id, name=package.manifest.name, description=package.manifest.description,
                        status="draft", enabled=True, draft_digest=package.archive_digest,
                        draft_manifest=manifest_json(package, unlocks), draft_revision=1,
                        has_unpublished_changes=True)
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as error:  # 并发安装同名技能：唯一约束兜底
                raise SkillConflict("skill_exists", f"技能 {row.name} 已存在，请更新它的草稿") from error
            await session.refresh(row)
            return skill_view(row)

    async def _ensure_new(self, session: AsyncSession, actor: Actor, key: str, name: str) -> None:
        names = (await session.scalars(select(Skill.name).where(Skill.workspace_id == actor.workspace_id,
                                                                Skill.scope_key == key))).all()
        if name in names:
            raise SkillConflict("skill_exists", f"技能 {name} 已存在，请更新它的草稿")
        if len(names) >= MAX_SKILLS_PER_SCOPE:
            raise SkillConflict("too_many_skills", f"同一范围最多 {MAX_SKILLS_PER_SCOPE} 个技能")

    async def replace_draft(self, actor: Actor, skill_id: str, archive: bytes, *, scope: str | None = None,
                            expected_revision: int | None = None) -> SkillView:
        package = await self._validate(archive)
        await self._store(package)  # 按摘要寻址、写入幂等：先于事务写入，不在持锁时上传
        async with self._tx(actor.workspace_id) as session:
            await self._lock_scope(session, actor.workspace_id)
            row = await self._load(session, actor, skill_id, scope, lock=True)
            self._authorize(actor, row, "edit")
            _check_revision(row, expected_revision)
            if package.manifest.name != row.name:
                raise PackageInvalid("name_mismatch", f"包里的技能名必须仍是 {row.name}", file="SKILL.md", field="name")
            claimed = await self._claims_in_scope(session, actor, row.scope_key, row.id)
            unlocks = check_claims(package.manifest, self._basic, claimed)
            row.description = package.manifest.description
            row.draft_digest = package.archive_digest
            row.draft_manifest = manifest_json(package, unlocks)
            row.draft_revision += 1
            row.has_unpublished_changes = package.archive_digest != row.published_digest
            await session.flush()
            await session.refresh(row)
            return skill_view(row)

    # ---- 读 ----

    async def list(self, actor: Actor, scope: str) -> tuple[SkillView, ...]:
        if scope not in SCOPES:
            raise PackageInvalid("invalid_scope", "scope 只能是 personal 或 workspace", field="scope")
        self._member(actor)
        async with self._tx(actor.workspace_id) as session:
            rows = (await session.scalars(select(Skill).where(
                Skill.workspace_id == actor.workspace_id, Skill.scope_key == scope_key(scope, actor.user_id))
                .order_by(Skill.name))).all()
        if scope == "personal":
            return tuple(skill_view(row) for row in rows)
        visible = self._access.visible_resources(actor.user_id, actor.workspace_id, "skill", "use")
        return tuple(skill_view(row) for row in rows if visible.allows(row.id))

    async def get(self, actor: Actor, skill_id: str, *,
                  scope: str | None = None) -> tuple[SkillView, tuple[ReleaseView, ...]]:
        async with self._tx(actor.workspace_id) as session:
            row = await self._load(session, actor, skill_id, scope)
            self._authorize(actor, row, "use")
            releases = (await session.scalars(select(SkillRelease).where(SkillRelease.skill_id == row.id)
                                              .order_by(SkillRelease.released_at.desc()).limit(50))).all()
            return skill_view(row), tuple(release_view(r) for r in releases)

    # ---- 启停、发布、删除 ----

    async def set_enabled(self, actor: Actor, skill_id: str, enabled: bool, *, scope: str | None = None) -> SkillView:
        async with self._tx(actor.workspace_id) as session:
            row = await self._load(session, actor, skill_id, scope, lock=True)
            self._authorize(actor, row, "edit")
            row.enabled = enabled
            await session.flush()
            await session.refresh(row)
            return skill_view(row)

    async def publish(self, actor: Actor, skill_id: str, *, scope: str | None = None, version: str | None = None,
                      expected_revision: int | None = None) -> ReleaseView:
        async with self._tx(actor.workspace_id) as session:
            await self._lock_scope(session, actor.workspace_id)
            row = await self._load(session, actor, skill_id, scope, lock=True)
            self._authorize(actor, row, "owner")
            _check_revision(row, expected_revision)
            if row.status == "published" and not row.has_unpublished_changes:
                raise SkillConflict("nothing_to_publish", "草稿与已发布版本相同，没有可发布的变更")
            claimed = await self._claims_in_scope(session, actor, row.scope_key, row.id)
            for tool in row.draft_manifest.get("unlocks", ()):
                if claimed.get(tool, row.name) != row.name:
                    raise SkillConflict("tool_already_claimed", f"工具 {tool} 已被技能 {claimed[tool]} 认领")
            label = _release_version(version, row)
            await self._ensure_version_free(session, row.id, label)
            release = SkillRelease(id=uuid.uuid4().hex, workspace_id=row.workspace_id, skill_id=row.id, version=label,
                                   archive_digest=row.draft_digest, manifest=dict(row.draft_manifest),
                                   released_by=actor.user_id)
            session.add(release)
            row.status, row.has_unpublished_changes = "published", False
            row.published_version, row.published_digest = label, row.draft_digest
            row.published_release_id, row.published_manifest = release.id, dict(row.draft_manifest)
            await session.flush()
            await session.refresh(release)
            return release_view(release)

    @staticmethod
    async def _ensure_version_free(session: AsyncSession, skill_id: str, version: str) -> None:
        taken = await session.scalar(select(SkillRelease.id).where(SkillRelease.skill_id == skill_id,
                                                                   SkillRelease.version == version))
        if taken is not None:
            raise SkillConflict("skill_version_exists", f"版本 {version} 已存在")

    async def delete(self, actor: Actor, skill_id: str, *, scope: str | None = None) -> None:
        async with self._tx(actor.workspace_id) as session:
            row = await self._load(session, actor, skill_id, scope, lock=True)
            self._authorize(actor, row, "owner")
            await session.execute(delete(SkillRelease).where(SkillRelease.skill_id == row.id))
            await session.delete(row)
            # 同一事务删掉指向它的授权，授权页不留孤儿
            change = await self._access.purge_grants(session, actor.workspace_id, resources=(f"skill:{row.id}",))
        await self._access.settle(change)


def _check_revision(row: Skill, expected: int | None) -> None:
    if expected is not None and expected != row.draft_revision:
        raise SkillConflict("revision_conflict", "草稿已被别人改过，请刷新后重试")


def _release_version(requested: str | None, row: Skill) -> str:
    label = (requested or "").strip() or str(row.draft_manifest.get("version") or "") or row.draft_digest[:12]
    if not VERSION.match(label):
        raise PackageInvalid("invalid_version", "版本号只能包含字母、数字与 ._+-，最长 64 个字符", field="version")
    return label
