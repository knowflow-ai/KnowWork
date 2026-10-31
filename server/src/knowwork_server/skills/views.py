"""服务对外返回的不可变视图，以及数据库行到视图的转换。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from .models import Skill, SkillRelease, SkillSubmission

Scope = Literal["personal", "workspace", "platform"]
SCOPES: tuple[str, ...] = ("personal", "workspace", "platform")


@dataclass(frozen=True)
class Actor:
    """操作者上下文：由服务端从已认证身份解析，不接受客户端或模型自报。"""

    user_id: str
    workspace_id: str


@dataclass(frozen=True)
class SkillView:
    id: str
    scope: str
    owner_id: str
    name: str
    description: str
    status: str
    enabled: bool
    draft_digest: str
    draft_revision: int
    has_unpublished_changes: bool
    published_version: str | None
    draft: dict[str, Any]
    published: dict[str, Any] | None
    updated_at: datetime | None
    audience: tuple[str, ...] | None = None  # 只有企业技能有：发给哪些空间，"*" 是全部
    env_filled: tuple[str, ...] = ()  # 已经填了值的环境变量（只有名字）

    def to_json(self) -> dict[str, Any]:
        raw = {"id": self.id, "scope": self.scope, "ownerId": self.owner_id, "name": self.name,
               "description": self.description, "status": self.status, "enabled": self.enabled,
               "draftDigest": self.draft_digest, "draftRevision": self.draft_revision,
               "hasUnpublishedChanges": self.has_unpublished_changes, "publishedVersion": self.published_version,
               "draft": self.draft, "published": self.published, "envFilled": list(self.env_filled),
               "updatedAt": self.updated_at.isoformat() if self.updated_at else None}
        return raw if self.audience is None else {**raw, "audience": list(self.audience)}


@dataclass(frozen=True)
class ReleaseView:
    id: str
    skill_id: str
    version: str
    archive_digest: str
    released_by: str
    released_at: datetime | None

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "skillId": self.skill_id, "version": self.version,
                "archiveDigest": self.archive_digest, "releasedBy": self.released_by,
                "releasedAt": self.released_at.isoformat() if self.released_at else None}


@dataclass(frozen=True)
class CatalogEntry:
    """本会话可用的一个技能（已发布版本）。交给宿主的字段见 to_host。"""

    skill_id: str
    release_id: str
    scope: str
    name: str
    description: str
    version: str
    digest: str  # 发布包摘要，宿主拉内容时带上，钉住会话打开时的版本
    content_digest: str  # SKILL.md 正文摘要，宿主据此校验拉到的内容
    allowed_tools: tuple[str, ...]
    completion_tools: tuple[str, ...]
    unlocks: tuple[str, ...]  # 本技能认领的专业工具：加载本技能之前对模型不可见
    model_invocable: bool
    user_invocable: bool
    env: tuple[str, ...] = ()  # 发布版本声明的环境变量名（值不在这里，也不给宿主）

    def to_host(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "version": self.version,
                "digest": self.digest, "contentDigest": self.content_digest,
                "allowedTools": list(self.allowed_tools), "completionTools": list(self.completion_tools),
                "unlocks": list(self.unlocks), "modelInvocable": self.model_invocable,
                "userInvocable": self.user_invocable, "scope": self.scope}


@dataclass(frozen=True)
class AvailableSkill:
    """对话里能用的一个技能（「技能」页的「可以用的」、@ 菜单）。"""

    name: str
    description: str
    source: str  # platform | workspace | personal | bundled
    version: str
    user_invocable: bool

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "source": self.source,
                "version": self.version, "userInvocable": self.user_invocable}


@dataclass(frozen=True)
class SkillContent:
    name: str
    version: str
    digest: str
    content_digest: str
    content: str

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "version": self.version, "digest": self.digest,
                "contentDigest": self.content_digest, "content": self.content}


def skill_view(row: Skill) -> SkillView:
    return SkillView(id=row.id, scope=row.scope, owner_id=row.owner_id, name=row.name, description=row.description,
                     status=row.status, enabled=row.enabled, draft_digest=row.draft_digest,
                     draft_revision=row.draft_revision, has_unpublished_changes=row.has_unpublished_changes,
                     published_version=row.published_version, draft=dict(row.draft_manifest),
                     published=dict(row.published_manifest) if row.published_manifest else None,
                     updated_at=row.updated_at,
                     audience=tuple(row.audience or ()) if row.scope == "platform" else None,
                     env_filled=tuple(row.env_filled or ()))


def release_view(row: SkillRelease) -> ReleaseView:
    return ReleaseView(id=row.id, skill_id=row.skill_id, version=row.version, archive_digest=row.archive_digest,
                       released_by=row.released_by, released_at=row.released_at)


def catalog_entry(row: Skill) -> CatalogEntry:
    """从已发布的技能行生成目录项。调用方保证 published_manifest 存在。"""
    manifest = row.published_manifest or {}
    return CatalogEntry(
        skill_id=row.id, release_id=row.published_release_id or "", scope=row.scope, name=row.name,
        description=str(manifest.get("description", row.description)), version=row.published_version or "",
        digest=row.published_digest or "", content_digest=str(manifest.get("contentDigest", "")),
        allowed_tools=tuple(manifest.get("allowedTools", ())), completion_tools=tuple(manifest.get("completionTools", ())),
        unlocks=tuple(manifest.get("unlocks", ())), model_invocable=bool(manifest.get("modelInvocable", True)),
        user_invocable=bool(manifest.get("userInvocable", True)),
        env=tuple(str(spec.get("name")) for spec in manifest.get("env", ()) if spec.get("name")))


@dataclass(frozen=True)
class SubmissionView:
    """个人技能提交到空间的一次。version、archive_digest、manifest 是提交那一刻发布版本的快照。"""

    id: str
    skill_id: str
    name: str
    version: str
    archive_digest: str
    manifest: dict[str, Any]
    note: str
    submitted_by: str
    status: str  # pending | approved | rejected | withdrawn
    review_note: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    target_skill_id: str | None
    created_at: datetime | None

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "skillId": self.skill_id, "name": self.name, "version": self.version,
                "archiveDigest": self.archive_digest, "manifest": self.manifest, "note": self.note,
                "submittedBy": self.submitted_by, "status": self.status, "reviewNote": self.review_note,
                "reviewedBy": self.reviewed_by,
                "reviewedAt": self.reviewed_at.isoformat() if self.reviewed_at else None,
                "targetSkillId": self.target_skill_id,
                "createdAt": self.created_at.isoformat() if self.created_at else None}


@dataclass(frozen=True)
class SubmissionTarget:
    """空间里已有的那份（之前从同一个个人技能复制来的）：审核时拿它的草稿和这次提交比。"""

    skill_id: str
    published_version: str | None
    has_unpublished_changes: bool
    manifest: dict[str, Any]
    content: str

    def to_json(self) -> dict[str, Any]:
        return {"skillId": self.skill_id, "publishedVersion": self.published_version,
                "hasUnpublishedChanges": self.has_unpublished_changes, "manifest": self.manifest,
                "content": self.content}


@dataclass(frozen=True)
class SubmissionDetail:
    submission: SubmissionView
    content: str  # 提交的 SKILL.md 正文
    target: SubmissionTarget | None
    name_taken: bool  # 空间里有同名、但不是从这个个人技能来的技能：通过不了

    def to_json(self) -> dict[str, Any]:
        return {**self.submission.to_json(), "content": self.content,
                "target": self.target.to_json() if self.target else None, "nameTaken": self.name_taken}


def submission_view(row: SkillSubmission) -> SubmissionView:
    return SubmissionView(id=row.id, skill_id=row.skill_id, name=row.name, version=row.version,
                          archive_digest=row.archive_digest, manifest=dict(row.manifest), note=row.note,
                          submitted_by=row.submitted_by, status=row.status, review_note=row.review_note,
                          reviewed_by=row.reviewed_by, reviewed_at=row.reviewed_at,
                          target_skill_id=row.target_skill_id, created_at=row.created_at)
