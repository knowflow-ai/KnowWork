"""本会话可用技能清单的编译，以及宿主拉取技能内容（技术方案 3.4、5.3 节）。

- 只有已发布、已启用的技能进入清单；空间技能按 RBAC 的 use 可见集合过滤，个人技能只给本人；
- 名称与认领的专业工具在清单内必须唯一：空间技能在前、个人技能在后，后者撞名或撞认领就跳过并记日志，
  不让一个技能的问题拖垮整张清单（技术方案 5.3 节「单个技能编译失败只跳过它」）；
- 拉内容时按此刻的授权重新编译清单，技能必须仍在清单里；请求的摘要可以是当前发布版本，
  也可以是该技能更早的发布版本（会话钉住打开时的版本），但不能是草稿或别的技能的包。
"""

import logging

from sqlalchemy import select

from ..access.rls import set_workspace
from .blobstore import BlobStore
from .errors import BlobIntegrityError, SkillNotFound
from .models import Skill, SkillRelease
from .service import AccessChecker, StorageUnavailable, scope_key
from .views import CatalogEntry, SkillContent, catalog_entry

log = logging.getLogger(__name__)

MAX_CATALOG = 64
MAX_CONTENT_BYTES = 1024 * 1024


class SkillCatalog:
    def __init__(self, sessionmaker, access: AccessChecker, blobs: BlobStore) -> None:
        self._sessionmaker = sessionmaker
        self._access = access
        self._blobs = blobs

    async def for_session(self, workspace_id: str, user_id: str) -> tuple[CatalogEntry, ...]:
        if not self._access.check(user_id, workspace_id, "workspace", "use"):
            return ()
        rows = await self._published(workspace_id, user_id)
        visible = self._access.visible_resources(user_id, workspace_id, "skill", "use")
        allowed = [row for row in rows if row.scope == "personal" or visible.allows(row.id)]
        return _deduplicate(allowed)

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
            release = await self._release(workspace_id, entry.skill_id, digest)
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


def _deduplicate(rows: list[Skill]) -> tuple[CatalogEntry, ...]:
    names: set[str] = set()
    claimed: dict[str, str] = {}
    entries: list[CatalogEntry] = []
    for row in rows:
        entry = catalog_entry(row)
        clash = next((tool for tool in entry.unlocks if tool in claimed), None)
        if entry.name in names or clash is not None or not entry.content_digest:
            log.warning("skill %s skipped from session catalog (name or claimed tool %s already taken)",
                        entry.name, clash)
            continue
        if len(entries) >= MAX_CATALOG:
            log.warning("session catalog truncated at %d skills", MAX_CATALOG)
            break
        names.add(entry.name)
        claimed.update({tool: entry.name for tool in entry.unlocks})
        entries.append(entry)
    return tuple(entries)
