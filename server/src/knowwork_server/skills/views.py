"""服务对外返回的不可变视图，以及数据库行到视图的转换。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from .models import Skill, SkillRelease

Scope = Literal["personal", "workspace"]
SCOPES: tuple[str, ...] = ("personal", "workspace")


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

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "scope": self.scope, "ownerId": self.owner_id, "name": self.name,
                "description": self.description, "status": self.status, "enabled": self.enabled,
                "draftDigest": self.draft_digest, "draftRevision": self.draft_revision,
                "hasUnpublishedChanges": self.has_unpublished_changes, "publishedVersion": self.published_version,
                "draft": self.draft, "published": self.published,
                "updatedAt": self.updated_at.isoformat() if self.updated_at else None}


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

    def to_host(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "version": self.version,
                "digest": self.digest, "contentDigest": self.content_digest,
                "allowedTools": list(self.allowed_tools), "completionTools": list(self.completion_tools),
                "unlocks": list(self.unlocks), "modelInvocable": self.model_invocable,
                "userInvocable": self.user_invocable, "scope": self.scope}


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
                     updated_at=row.updated_at)


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
        user_invocable=bool(manifest.get("userInvocable", True)))
