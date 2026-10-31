"""个人技能提交到空间与审核：往上走一律是「提交 → 空间管理员审 → 复制一份」，不改个人技能的可见范围。

- 作者提交的是个人技能当前的发布版本，存下包摘要与清单的快照，之后再改、再删都不影响这次提交；
  同一个个人技能同时只有一份待审，提交新版本时顶掉旧的那份，同一版再提交只改附言；
- 第一次通过在空间里新建一个技能（所有者记审核人，origin_skill_id 指回个人技能），以后的提交写成它的新草稿；
  包按摘要引用同一份存储，不复制文件。通过时可以直接发布，否则进草稿，管理员改完授权再发布；
- 空间里有同名、但不是从这个个人技能来的技能时，提交那一步就拦下，通过时再查一次（中间可能有人装了同名的）；
- 审核要对全部技能有 owner（与安装空间技能同一道门）；提交、撤回只认本人的个人技能，别人的当作不存在。
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.rls import set_workspace
from .blobstore import BlobStore
from .errors import BlobIntegrityError, PackageInvalid, SkillConflict, SkillForbidden, SkillNotFound
from .models import Skill, SkillSubmission
from .package import PackageLimits
from .service import (
    EVERYONE,
    MAX_SKILLS_PER_SCOPE,
    AccessChecker,
    StorageUnavailable,
    claimed_tools,
    ensure_unclaimed,
    lock_scope,
    publish_current,
)
from .views import (
    Actor,
    SkillView,
    SubmissionDetail,
    SubmissionTarget,
    SubmissionView,
    skill_view,
    submission_view,
)

MAX_NOTE = 500
MAX_CONTENT_BYTES = 1024 * 1024
MAX_PENDING_LISTED = 200


def _text(value: str, *, code: str, label: str, required: bool) -> str:
    cleaned = (value or "").strip()
    if required and not cleaned:
        raise PackageInvalid(code, f"请写{label}")
    if len(cleaned) > MAX_NOTE:
        raise PackageInvalid(code, f"{label}最多 {MAX_NOTE} 个字")
    return cleaned


class SubmissionService:
    def __init__(self, sessionmaker: async_sessionmaker, access: AccessChecker, blobs: BlobStore, *,
                 limits: PackageLimits | None = None) -> None:
        self._sessionmaker = sessionmaker
        self._access = access
        self._blobs = blobs
        self._limits = limits or PackageLimits()

    @asynccontextmanager
    async def _tx(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    def _member(self, actor: Actor) -> None:
        if not self._access.check(actor.user_id, actor.workspace_id, "workspace", "use"):
            raise SkillForbidden()

    def _reviewer(self, actor: Actor) -> None:
        self._member(actor)
        if not self._access.visible_resources(actor.user_id, actor.workspace_id, "skill", "owner").all_:
            raise SkillForbidden("只有空间管理员可以审核技能")

    # ---- 作者 ----

    async def submit(self, actor: Actor, skill_id: str, note: str = "") -> SubmissionView:
        note = _text(note, code="invalid_note", label="附言", required=False)
        self._member(actor)
        async with self._tx(actor.workspace_id) as session:
            await lock_scope(session, actor.workspace_id)
            row = await self._own(session, actor, skill_id, lock=True)
            if row.status != "published" or not row.published_digest:
                raise SkillConflict("not_published", "这个技能还没有可以提交的版本")
            await self._check_target(session, row)
            pending = await self._pending_of(session, row.id)
            if pending is not None and pending.archive_digest == row.published_digest:
                pending.note = note
                await session.flush()
                return submission_view(pending)
            if pending is not None:
                pending.status = "withdrawn"
                await session.flush()  # 先让出「一个技能只有一份待审」的唯一索引
            submission = SkillSubmission(
                id=uuid.uuid4().hex, workspace_id=actor.workspace_id, skill_id=row.id, name=row.name,
                version=row.published_version or row.published_digest[:12], archive_digest=row.published_digest,
                manifest=dict(row.published_manifest or {}), note=note, submitted_by=actor.user_id,
                status="pending", review_note="")
            session.add(submission)
            await session.flush()
            await session.refresh(submission)
            return submission_view(submission)

    async def _check_target(self, session: AsyncSession, row: Skill) -> None:
        target = await _target(session, row.workspace_id, row.id)
        if target is None:
            if await _name_taken(session, row.workspace_id, row.name):
                raise SkillConflict("skill_exists", f"空间里已经有同名技能「{row.name}」，改个名字再提交")
        elif target.draft_digest == row.published_digest:
            raise SkillConflict("already_in_workspace", "空间里已经是这一版了")

    async def withdraw(self, actor: Actor, skill_id: str) -> None:
        self._member(actor)
        async with self._tx(actor.workspace_id) as session:
            row = await self._own(session, actor, skill_id)
            pending = await self._pending_of(session, row.id)
            if pending is None:
                raise SkillConflict("no_pending", "没有待审的提交")
            pending.status = "withdrawn"

    async def latest(self, actor: Actor) -> dict[str, SubmissionView]:
        """本人每个个人技能最近一次提交（「我的技能」里显示审核状态用）。"""
        self._member(actor)
        async with self._tx(actor.workspace_id) as session:
            rows = (await session.scalars(select(SkillSubmission).where(
                SkillSubmission.workspace_id == actor.workspace_id, SkillSubmission.submitted_by == actor.user_id)
                .order_by(SkillSubmission.created_at.desc()))).all()
        latest: dict[str, SubmissionView] = {}
        for row in rows:
            latest.setdefault(row.skill_id, submission_view(row))
        return latest

    async def _own(self, session: AsyncSession, actor: Actor, skill_id: str, *, lock: bool = False) -> Skill:
        stmt = select(Skill).where(Skill.id == skill_id, Skill.workspace_id == actor.workspace_id)
        row = await session.scalar(stmt.with_for_update() if lock else stmt)
        if row is None or row.scope != "personal" or row.owner_id != actor.user_id:
            raise SkillNotFound()
        return row

    @staticmethod
    async def _pending_of(session: AsyncSession, skill_id: str) -> SkillSubmission | None:
        return await session.scalar(select(SkillSubmission).where(
            SkillSubmission.skill_id == skill_id, SkillSubmission.status == "pending").with_for_update())

    # ---- 审核 ----

    async def pending(self, actor: Actor) -> tuple[SubmissionView, ...]:
        self._reviewer(actor)
        async with self._tx(actor.workspace_id) as session:
            rows = (await session.scalars(select(SkillSubmission).where(
                SkillSubmission.workspace_id == actor.workspace_id, SkillSubmission.status == "pending")
                .order_by(SkillSubmission.created_at).limit(MAX_PENDING_LISTED))).all()
        return tuple(submission_view(row) for row in rows)

    async def detail(self, actor: Actor, submission_id: str) -> SubmissionDetail:
        self._reviewer(actor)
        async with self._tx(actor.workspace_id) as session:
            row = await self._load(session, actor, submission_id)
            target = await _target(session, actor.workspace_id, row.skill_id)
            taken = target is None and await _name_taken(session, actor.workspace_id, row.name)
        content = await self._content(row.manifest)
        if target is None:
            return SubmissionDetail(submission=submission_view(row), content=content, target=None, name_taken=taken)
        current = SubmissionTarget(skill_id=target.id, published_version=target.published_version,
                                   has_unpublished_changes=target.has_unpublished_changes,
                                   manifest=dict(target.draft_manifest), content=await self._content(target.draft_manifest))
        return SubmissionDetail(submission=submission_view(row), content=content, target=current, name_taken=False)

    async def archive(self, actor: Actor, submission_id: str) -> tuple[str, bytes]:
        """提交的技能包原样下载，审核时看脚本用。"""
        self._reviewer(actor)
        async with self._tx(actor.workspace_id) as session:
            row = await self._load(session, actor, submission_id)
        try:
            body = await self._blobs.get(row.archive_digest, max_bytes=self._limits.max_archive_bytes)
        except BlobIntegrityError as error:
            raise StorageUnavailable() from error
        return f"{row.name}-{row.version}.zip", body

    async def approve(self, actor: Actor, submission_id: str, *, publish: bool = False) -> SkillView:
        self._reviewer(actor)
        async with self._tx(actor.workspace_id) as session:
            await lock_scope(session, actor.workspace_id)
            row = await self._open(session, actor, submission_id)
            target = await _target(session, actor.workspace_id, row.skill_id)
            claimed = await claimed_tools(session, actor.workspace_id, {"workspace"}, target.id if target else None)
            ensure_unclaimed(row.manifest, row.name, claimed)
            first = target is None
            target = await self._copy(session, actor, row, target)
            # 第一次进空间同新建的空间技能，默认全体成员；以后的版本保留原来的「给谁用」
            change = await self._access.seed_users(session, actor.workspace_id, f"skill:{target.id}", (EVERYONE,)) \
                if first else None
            if publish:
                await publish_current(session, target, actor.user_id, preferred=row.version)
            row.status, row.reviewed_by, row.reviewed_at = "approved", actor.user_id, datetime.now(UTC)
            row.target_skill_id = target.id
            await session.flush()
            await session.refresh(target)
            view = skill_view(target)
        if change is not None:
            await self._access.settle(change)
        return view

    async def _copy(self, session: AsyncSession, actor: Actor, row: SkillSubmission, target: Skill | None) -> Skill:
        """第一次通过新建空间技能，以后写成它的新草稿。"""
        description = str(row.manifest.get("description", ""))
        if target is not None:
            target.description = description
            target.draft_digest, target.draft_manifest = row.archive_digest, dict(row.manifest)
            target.draft_revision += 1
            target.has_unpublished_changes = row.archive_digest != target.published_digest
            return target
        if await _name_taken(session, actor.workspace_id, row.name):
            raise SkillConflict("skill_exists", f"空间里已经有同名技能「{row.name}」，这份提交通过不了")
        count = len((await session.scalars(select(Skill.id).where(Skill.workspace_id == actor.workspace_id,
                                                                   Skill.scope_key == "workspace"))).all())
        if count >= MAX_SKILLS_PER_SCOPE:
            raise SkillConflict("too_many_skills", f"同一范围最多 {MAX_SKILLS_PER_SCOPE} 个技能")
        target = Skill(id=uuid.uuid4().hex, workspace_id=actor.workspace_id, scope="workspace",
                       scope_key="workspace", owner_id=actor.user_id, name=row.name, description=description,
                       status="draft", enabled=True, draft_digest=row.archive_digest,
                       draft_manifest=dict(row.manifest), draft_revision=1, has_unpublished_changes=True,
                       origin_skill_id=row.skill_id)
        session.add(target)
        try:
            await session.flush()
        except IntegrityError as error:  # 唯一约束兜底
            raise SkillConflict("skill_exists", f"空间里已经有同名技能「{row.name}」") from error
        return target

    async def reject(self, actor: Actor, submission_id: str, reason: str) -> SubmissionView:
        reason = _text(reason, code="invalid_reason", label="不通过的原因", required=True)
        self._reviewer(actor)
        async with self._tx(actor.workspace_id) as session:
            row = await self._open(session, actor, submission_id)
            row.status, row.review_note = "rejected", reason
            row.reviewed_by, row.reviewed_at = actor.user_id, datetime.now(UTC)
            await session.flush()
            await session.refresh(row)
            return submission_view(row)

    async def _load(self, session: AsyncSession, actor: Actor, submission_id: str, *,
                    lock: bool = False) -> SkillSubmission:
        stmt = select(SkillSubmission).where(SkillSubmission.id == submission_id,
                                             SkillSubmission.workspace_id == actor.workspace_id)
        row = await session.scalar(stmt.with_for_update() if lock else stmt)
        if row is None:
            raise SkillNotFound("提交记录不存在")
        return row

    async def _open(self, session: AsyncSession, actor: Actor, submission_id: str) -> SkillSubmission:
        row = await self._load(session, actor, submission_id, lock=True)
        if row.status != "pending":
            raise SkillConflict("submission_closed", "这份提交已经处理过了")
        return row

    async def _content(self, manifest: dict) -> str:
        digest = str(manifest.get("contentDigest", ""))
        if not digest:
            return ""
        try:
            return (await self._blobs.get(digest, max_bytes=MAX_CONTENT_BYTES)).decode("utf-8")
        except (BlobIntegrityError, UnicodeDecodeError) as error:
            raise StorageUnavailable() from error


async def _target(session: AsyncSession, workspace_id: str, origin_id: str) -> Skill | None:
    return await session.scalar(select(Skill).where(Skill.workspace_id == workspace_id, Skill.scope_key == "workspace",
                                                    Skill.origin_skill_id == origin_id).limit(1))


async def _name_taken(session: AsyncSession, workspace_id: str, name: str) -> bool:
    return await session.scalar(select(Skill.id).where(Skill.workspace_id == workspace_id,
                                                       Skill.scope_key == "workspace", Skill.name == name)) is not None
