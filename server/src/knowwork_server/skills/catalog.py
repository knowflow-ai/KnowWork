"""本会话可用技能清单的编译，以及宿主拉取技能内容。

- 只有已发布、已启用的技能进入清单。三段：企业技能（发到本空间、本空间没停用、没收窄或收窄后这个人在内，layers.py）、
  空间技能（按 RBAC 的 use 可见集合过滤）、个人技能（只给本人）；
- 名称与认领的专业工具在清单内必须唯一：上层优先（企业 > 空间 > 个人），下层撞名或撞认领就跳过并记日志，
  不让一个技能的问题拖垮整张清单（单个技能编译失败只跳过它）；「我的技能」与空间技能列表按同一套规则标出没生效的
  （inactive_personal、inactive_workspace），不另写一套判断；
- 这个人在本空间用不了的内置技能（停用了、或收窄后不在内，bundled_off）随清单一起给宿主，宿主不注册它们；
- 拉内容时按此刻的授权重新编译清单，技能必须仍在清单里；请求的摘要可以是当前发布版本，
  也可以是该技能更早的发布版本（会话钉住打开时的版本），但不能是草稿或别的技能的包。
"""

import logging

from sqlalchemy import select

from ..access.rls import set_workspace
from ..access.workspaces import PLATFORM_WORKSPACE_ID
from .blobstore import BlobStore
from .errors import BlobIntegrityError, SkillNotFound
from .layers import BUNDLED, DEFAULT, PLATFORM, platform_rows, settings_of, usable
from .models import Skill, SkillRelease
from .service import AccessChecker, StorageUnavailable, scope_key
from .views import CatalogEntry, SkillContent, catalog_entry

log = logging.getLogger(__name__)

MAX_CATALOG = 64
MAX_CONTENT_BYTES = 1024 * 1024

# 没进清单的技能：技能 id → (范围, 原因)
Skipped = dict[str, tuple[str, str]]


class SkillCatalog:
    def __init__(self, sessionmaker, access: AccessChecker, blobs: BlobStore) -> None:
        self._sessionmaker = sessionmaker
        self._access = access
        self._blobs = blobs

    async def for_session(self, workspace_id: str, user_id: str) -> tuple[CatalogEntry, ...]:
        entries, _ = await self._compile(workspace_id, user_id)
        return entries

    async def inactive_personal(self, workspace_id: str, user_id: str) -> dict[str, str]:
        """本人已发布、已启用、却进不了会话的个人技能：技能 id → 原因（name_taken 同名的上层技能优先、
        tool_claimed 认领的工具已被上层技能认领、limit 清单满了）。"""
        _, skipped = await self._compile(workspace_id, user_id)
        return {skill_id: reason for skill_id, (scope, reason) in skipped.items() if scope == "personal"}

    async def inactive_workspace(self, workspace_id: str) -> dict[str, str]:
        """已发布、已启用、却被企业技能顶掉的空间技能（不看授权与收窄：企业技能收窄后，用不了它的人那里同名的空间技能
        照常生效，这里仍标出来提醒管理员）。"""
        rows = await self._published(workspace_id, "")
        workspace = [row for row in rows if row.scope == "workspace"]
        _, skipped = _deduplicate([*await self._platform(workspace_id, None), *workspace])
        return {skill_id: reason for skill_id, (scope, reason) in skipped.items() if scope == "workspace"}

    async def bundled_off(self, workspace_id: str, user_id: str) -> tuple[str, ...]:
        """这个人在本空间用不了的内置技能名字（停用了，或收窄后不在内）。"""
        settings = await settings_of(self._sessionmaker, workspace_id)
        return tuple(sorted(key[len(BUNDLED):] for key, setting in settings.items()
                            if key.startswith(BUNDLED) and not usable(self._access, workspace_id, user_id, key, setting)))

    async def _compile(self, workspace_id: str, user_id: str) -> tuple[tuple[CatalogEntry, ...], Skipped]:
        if not self._access.check(user_id, workspace_id, "workspace", "use"):
            return (), {}
        rows = await self._published(workspace_id, user_id)
        visible = self._access.visible_resources(user_id, workspace_id, "skill", "use")
        allowed = [row for row in rows if row.scope == "personal" or visible.allows(row.id)]
        return _deduplicate([*await self._platform(workspace_id, user_id), *allowed])

    async def _platform(self, workspace_id: str, user_id: str | None) -> list[Skill]:
        """本空间没停用的企业技能；给了人时再按收窄过滤（None 是不分人的管理视角）。"""
        settings = await settings_of(self._sessionmaker, workspace_id)

        def keep(row: Skill) -> bool:
            key = f"{PLATFORM}{row.id}"
            setting = settings.get(key, DEFAULT)
            return not setting.off if user_id is None else usable(self._access, workspace_id, user_id, key, setting)

        return [row for row in await platform_rows(self._sessionmaker, workspace_id) if keep(row)]

    async def _published(self, workspace_id: str, user_id: str) -> list[Skill]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            rows = (await session.scalars(select(Skill).where(
                Skill.workspace_id == workspace_id, Skill.enabled.is_(True), Skill.status == "published",
                Skill.scope_key.in_(("workspace", scope_key("personal", user_id)))))).all()
        # 空间技能优先，其次按名称，保证跳过规则确定
        return sorted(rows, key=lambda row: (row.scope != "workspace", row.name))

    async def load(self, workspace_id: str, user_id: str, name: str, digest: str) -> SkillContent:
        entry = next((e for e in await self.for_session(workspace_id, user_id) if e.name == name), None)
        if entry is None:
            raise SkillNotFound("技能不在本会话的可用范围内")
        version, content_digest = entry.version, entry.content_digest
        if digest != entry.digest:
            home = PLATFORM_WORKSPACE_ID if entry.scope == "platform" else workspace_id  # 企业技能的发布记录在平台空间
            release = await self._release(home, entry.skill_id, digest)
            if release is None:
                raise SkillNotFound("技能版本不在本会话的可用范围内")
            version, content_digest = release.version, str(release.manifest.get("contentDigest", ""))
        try:
            body = await self._blobs.get(content_digest, max_bytes=MAX_CONTENT_BYTES)
        except BlobIntegrityError as error:
            log.error("skill %s content %s unreadable: %s", name, content_digest, error)
            raise StorageUnavailable() from error
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise StorageUnavailable() from error
        return SkillContent(name=name, version=version, digest=digest, content_digest=content_digest, content=text)

    async def _release(self, workspace_id: str, skill_id: str, digest: str) -> SkillRelease | None:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            return await session.scalar(select(SkillRelease).where(
                SkillRelease.skill_id == skill_id, SkillRelease.archive_digest == digest).limit(1))


def _deduplicate(rows: list[Skill]) -> tuple[tuple[CatalogEntry, ...], Skipped]:
    names: set[str] = set()
    claimed: dict[str, str] = {}
    entries: list[CatalogEntry] = []
    skipped: Skipped = {}
    for row in rows:
        entry = catalog_entry(row)
        clash = next((tool for tool in entry.unlocks if tool in claimed), None)
        if entry.name in names or clash is not None or not entry.content_digest:
            log.warning("skill %s skipped from session catalog (name or claimed tool %s already taken)",
                        entry.name, clash)
            reason = "name_taken" if entry.name in names else "tool_claimed" if clash else "invalid"
            skipped[row.id] = (row.scope, reason)
            continue
        if len(entries) >= MAX_CATALOG:
            skipped[row.id] = (row.scope, "limit")
            continue
        names.add(entry.name)
        claimed.update({tool: entry.name for tool in entry.unlocks})
        entries.append(entry)
    if any(reason == "limit" for _, reason in skipped.values()):
        log.warning("session catalog truncated at %d skills", MAX_CATALOG)
    return tuple(entries), skipped
