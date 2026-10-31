"""技能的安装、草稿、发布、启停与删除。

状态机：
- 安装：新建技能，status = draft，draft_revision = 1，has_unpublished_changes = 真；
- 更新草稿：换一个包，draft_revision + 1；已发布的技能同时把 has_unpublished_changes 置真；
- 发布：把当前草稿冻结成一条不可变的 skill_release，status = published，has_unpublished_changes = 假；
  版本号取请求给的，或 frontmatter 的 version，都没有时取包摘要前 12 位；(skill_id, version) 重复即 409；
- 个人技能上传即发布（安装、换包都直接生效）：对话里只用发布版本，草稿没法先试，多一步发布只是多点一下；
- 启停只影响是否进入运行配置；删除连同发布记录一起删（运行事实保留）。

范围与权限：
- 企业技能（scope = platform）存在平台配置所在的空间名下，只有系统管理员能看能改（扩展的「企业」页签经 platform.py 调这里）；
- 空间技能（scope = workspace）按 access 的 RBAC：新建时默认全体成员可用（授 role:editor use，和 MCP 服务一致），
  在技能自己的设置里收窄「给谁用」；安装要对全部技能有 owner（空间 owner、admin），
  读要 use，改草稿、启停要 edit，发布、删除要 owner；
- 个人技能（scope = personal）只有本人可见可用，且本人必须仍是空间成员；别人一律当作不存在。
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Protocol

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.engine import VisibleSet
from ..access.rls import set_workspace
from ..access.rules import RuleChange
from ..access.workspaces import PLATFORM_WORKSPACE_ID
from .blobstore import BlobStore
from .claims import DEFAULT_BASIC_TOOLS, check_claims
from .env import EnvBox
from .errors import BlobIntegrityError, PackageInvalid, SkillConflict, SkillError, SkillForbidden, SkillNotFound
from .manifest import VERSION
from .models import Skill, SkillRelease, SkillSubmission
from .package import PackageLimits, ValidatedPackage, validate_skill_zip
from .views import SCOPES, Actor, ReleaseView, SkillView, release_view, skill_view

MAX_SKILLS_PER_SCOPE = 500
EVERYONE = "role:editor"  # 「全体成员」：成员角色；所有者、管理员对全部技能本来就有权限


class AccessChecker(Protocol):
    """access.service.AccessService 满足这个协议。"""

    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...

    def is_system_admin(self, user_id: str) -> bool: ...

    def visible_resources(self, user_id: str, workspace_id: str, resource_type: str, action: str) -> VisibleSet: ...

    async def purge_grants(self, session: AsyncSession, workspace_id: str, *, resources: tuple[str, ...] = (),
                           prefixes: tuple[str, ...] = ()) -> RuleChange: ...

    async def seed_users(self, session: AsyncSession, workspace_id: str, resource: str,
                         subjects: Sequence[str]) -> RuleChange: ...

    async def settle(self, change: RuleChange) -> None: ...

    async def set_users(self, actor_id: str, workspace_id: str, resource: str,
                        subjects: Sequence[str]) -> tuple[str, ...]: ...


class StorageUnavailable(SkillError):
    status = 503

    def __init__(self) -> None:
        super().__init__("storage_unavailable", "技能存储暂时不可用，请稍后再试")


def scope_key(scope: str, user_id: str) -> str:
    return scope if scope in ("workspace", "platform") else f"user:{user_id}"


async def lock_scope(session: AsyncSession, workspace_id: str) -> None:
    """同一空间的安装、改草稿、发布、审核串行：名称查重与工具认领检查不能被并发绕过。"""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"skill:{workspace_id}"})


async def claimed_tools(session: AsyncSession, workspace_id: str, keys: set[str],
                        exclude_id: str | None) -> dict[str, str]:
    """这些范围里其他技能已认领的专业工具（工具 → 技能名），草稿与发布版本都算。"""
    rows = (await session.scalars(select(Skill).where(Skill.workspace_id == workspace_id,
                                                      Skill.scope_key.in_(keys)))).all()
    claimed: dict[str, str] = {}
    for row in rows:
        if row.id == exclude_id:
            continue
        for manifest in (row.draft_manifest, row.published_manifest or {}):
            for tool in manifest.get("unlocks", ()):
                claimed.setdefault(tool, row.name)
    return claimed


def ensure_unclaimed(manifest: dict[str, Any], name: str, claimed: dict[str, str]) -> None:
    for tool in manifest.get("unlocks", ()):
        if claimed.get(tool, name) != name:
            raise SkillConflict("tool_already_claimed", f"工具 {tool} 已被技能 {claimed[tool]} 认领")


async def _version_taken(session: AsyncSession, skill_id: str, version: str) -> bool:
    return await session.scalar(select(SkillRelease.id).where(SkillRelease.skill_id == skill_id,
                                                              SkillRelease.version == version)) is not None


def _point(row: Skill, release: SkillRelease) -> None:
    row.status, row.has_unpublished_changes = "published", False
    row.published_version, row.published_digest = release.version, release.archive_digest
    row.published_release_id, row.published_manifest = release.id, dict(release.manifest)


async def freeze(session: AsyncSession, row: Skill, label: str, user_id: str) -> SkillRelease:
    """把当前草稿冻结成一条发布版本，并让技能指向它。"""
    if await _version_taken(session, row.id, label):
        raise SkillConflict("skill_version_exists", f"版本 {label} 已存在")
    release = SkillRelease(id=uuid.uuid4().hex, workspace_id=row.workspace_id, skill_id=row.id, version=label,
                           archive_digest=row.draft_digest, manifest=dict(row.draft_manifest), released_by=user_id)
    session.add(release)
    _point(row, release)
    return release


async def publish_current(session: AsyncSession, row: Skill, user_id: str, *, preferred: str = "") -> None:
    """不问版本号直接发布当前草稿（个人技能上传即生效、审核通过时勾了直接发布）。

    草稿和以前某个发布版本是同一个包时，直接用回那个版本，不重复记一条；否则版本号先用 preferred
    （提交时的版本），再用包里写的，用过了或不合格就按包摘要编号。
    """
    if row.status == "published" and not row.has_unpublished_changes:
        return
    same = await session.scalar(select(SkillRelease).where(SkillRelease.skill_id == row.id,
                                                           SkillRelease.archive_digest == row.draft_digest)
                                .order_by(SkillRelease.released_at.desc()).limit(1))
    if same is not None:
        _point(row, same)
        return
    label = preferred.strip() or str(row.draft_manifest.get("version") or "")
    if not VERSION.match(label) or await _version_taken(session, row.id, label):
        label = row.draft_digest[:12]
    await freeze(session, row, label, user_id)


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
                 max_concurrent_validations: int = 2, env_box: EnvBox | None = None) -> None:
        self._sessionmaker = sessionmaker
        self._access = access
        self._blobs = blobs
        self._env = env_box
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

    def _system(self, actor: Actor) -> None:
        if actor.workspace_id != PLATFORM_WORKSPACE_ID or not self._access.is_system_admin(actor.user_id):
            raise SkillForbidden("只有系统管理员可以管理企业技能")

    def _authorize(self, actor: Actor, row: Skill, action: str) -> None:
        """个人技能只有本人；空间技能看 RBAC；企业技能只有系统管理员。看不到的一律 404，有读权限但动作不够才是 403。"""
        if row.scope == "platform":
            if actor.workspace_id != PLATFORM_WORKSPACE_ID or not self._access.is_system_admin(actor.user_id):
                raise SkillNotFound()
            return
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
            raise PackageInvalid("invalid_scope", "scope 只能是 personal、workspace 或 platform", field="scope")
        if scope == "platform":
            self._system(actor)
            return
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

    @staticmethod
    async def _claims_in_scope(session: AsyncSession, actor: Actor, key: str,
                               exclude_id: str | None) -> dict[str, str]:
        """同一范围（空间技能与该用户的个人技能视为同一个认领空间；企业技能自成一个）里其他技能已认领的专业工具。
        跨层的撞认领在会话清单里按上层优先处理。"""
        keys = {"platform"} if key == "platform" else {"workspace", key}
        return await claimed_tools(session, actor.workspace_id, keys, exclude_id)

    async def _validate(self, archive: bytes) -> ValidatedPackage:
        """解压与逐个算哈希是 CPU 活，放到线程里；同时校验的包数有上限，防止上传把服务拖死。"""
        async with self._validating:
            return await asyncio.to_thread(validate_skill_zip, archive, self._limits)

    # ---- 安装与草稿 ----

    async def install(self, actor: Actor, scope: str, archive: bytes) -> SkillView:
        self._require_install(actor, scope)
        package = await self._validate(archive)
        await self._store(package)  # 按摘要寻址、写入幂等：先于事务写入，不在持锁时上传
        key = scope_key(scope, actor.user_id)
        async with self._tx(actor.workspace_id) as session:
            await lock_scope(session, actor.workspace_id)
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
                raise SkillConflict("skill_exists", _exists_message(key, row.name)) from error
            if scope == "personal":
                await publish_current(session, row, actor.user_id)
                await session.flush()
            change = await self._access.seed_users(session, actor.workspace_id, f"skill:{row.id}", (EVERYONE,)) \
                if scope == "workspace" else None
            await session.refresh(row)
            view = skill_view(row)
        if change is not None:
            await self._access.settle(change)
        return view

    async def _ensure_new(self, session: AsyncSession, actor: Actor, key: str, name: str) -> None:
        names = (await session.scalars(select(Skill.name).where(Skill.workspace_id == actor.workspace_id,
                                                                Skill.scope_key == key))).all()
        if name in names:
            raise SkillConflict("skill_exists", _exists_message(key, name))
        if len(names) >= MAX_SKILLS_PER_SCOPE:
            raise SkillConflict("too_many_skills", f"同一范围最多 {MAX_SKILLS_PER_SCOPE} 个技能")

    async def replace_draft(self, actor: Actor, skill_id: str, archive: bytes, *, scope: str | None = None,
                            expected_revision: int | None = None) -> SkillView:
        package = await self._validate(archive)
        await self._store(package)  # 按摘要寻址、写入幂等：先于事务写入，不在持锁时上传
        async with self._tx(actor.workspace_id) as session:
            await lock_scope(session, actor.workspace_id)
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
            if row.scope == "personal":
                await publish_current(session, row, actor.user_id)
            await session.flush()
            await session.refresh(row)
            return skill_view(row)

    # ---- 读 ----

    async def list(self, actor: Actor, scope: str) -> tuple[SkillView, ...]:
        if scope not in SCOPES:
            raise PackageInvalid("invalid_scope", "scope 只能是 personal、workspace 或 platform", field="scope")
        if scope == "platform":
            self._system(actor)
        else:
            self._member(actor)
        async with self._tx(actor.workspace_id) as session:
            rows = (await session.scalars(select(Skill).where(
                Skill.workspace_id == actor.workspace_id, Skill.scope_key == scope_key(scope, actor.user_id))
                .order_by(Skill.name))).all()
        if scope in ("personal", "platform"):
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
            await lock_scope(session, actor.workspace_id)
            row = await self._load(session, actor, skill_id, scope, lock=True)
            self._authorize(actor, row, "owner")
            _check_revision(row, expected_revision)
            if row.status == "published" and not row.has_unpublished_changes:
                raise SkillConflict("nothing_to_publish", "草稿与已发布版本相同，没有可发布的变更")
            ensure_unclaimed(row.draft_manifest, row.name,
                             await self._claims_in_scope(session, actor, row.scope_key, row.id))
            release = await freeze(session, row, _release_version(version, row), actor.user_id)
            await session.flush()
            await session.refresh(release)
            return release_view(release)

    async def delete(self, actor: Actor, skill_id: str, *, scope: str | None = None) -> None:
        async with self._tx(actor.workspace_id) as session:
            row = await self._load(session, actor, skill_id, scope, lock=True)
            self._authorize(actor, row, "owner")
            await session.execute(delete(SkillRelease).where(SkillRelease.skill_id == row.id))
            await session.delete(row)
            # 同一事务删掉指向它的授权，授权页不留孤儿
            change = await self._access.purge_grants(session, actor.workspace_id, resources=(f"skill:{row.id}",))
        await self._access.settle(change)

    async def set_env(self, actor: Actor, skill_id: str, changes: dict[str, str | None], *,
                      scope: str | None = None) -> SkillView:
        """填或清技能的环境变量（值只写不读）。要对技能有 owner：企业技能系统管理员、空间技能空间管理员、个人技能本人。"""
        if self._env is None:
            raise StorageUnavailable()
        async with self._tx(actor.workspace_id) as session:
            row = await self._load(session, actor, skill_id, scope, lock=True)
            self._authorize(actor, row, "owner")
            self._env.change(row, changes)
            await session.flush()
            await session.refresh(row)
            return skill_view(row)

    @staticmethod
    async def forget_member(session: AsyncSession, workspace_id: str, user_id: str) -> None:
        """成员被移出空间（在移出的同一个事务里调用）：删掉他在这个空间的个人技能，连同发布记录与提交记录。
        已经审核通过、复制进空间的那份归空间，不受影响。个人技能没有授权，不用清授权。"""
        ids = (await session.scalars(select(Skill.id).where(
            Skill.workspace_id == workspace_id, Skill.scope_key == scope_key("personal", user_id)))).all()
        if not ids:
            return
        for table in (SkillSubmission, SkillRelease):
            await session.execute(delete(table).where(table.skill_id.in_(ids)))
        await session.execute(delete(Skill).where(Skill.id.in_(ids)))


def _exists_message(key: str, name: str) -> str:
    if key == "workspace":
        return f"技能 {name} 已存在，请更新它的草稿"
    if key == "platform":
        return f"企业技能 {name} 已存在，请更新它的草稿"
    return f"你已经有名为 {name} 的技能，要换内容请给它上传新版本"


def _check_revision(row: Skill, expected: int | None) -> None:
    if expected is not None and expected != row.draft_revision:
        raise SkillConflict("revision_conflict", "草稿已被别人改过，请刷新后重试")


def _release_version(requested: str | None, row: Skill) -> str:
    label = (requested or "").strip() or str(row.draft_manifest.get("version") or "") or row.draft_digest[:12]
    if not VERSION.match(label):
        raise PackageInvalid("invalid_version", "版本号只能包含字母、数字与 ._+-，最长 64 个字符", field="version")
    return label
