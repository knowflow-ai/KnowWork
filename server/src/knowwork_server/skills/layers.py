"""上层技能在某个空间里的情况：企业技能（系统管理员维护、发给选定或全部空间）与宿主内置技能（发给全部空间）。
界面上统一叫「企业」，代码里仍叫 platform。

- 企业技能是 skill 表里 scope = platform 的行，存在平台配置所在的空间（PLATFORM_WORKSPACE_ID）名下，
  audience 是发给哪些空间（"*" 是全部空间，以后新建的也有）；
- 发到的空间里默认全体成员都能用；空间管理员可以在本空间停用、收窄给谁用，不能改。设置记在 skill_workspace_setting
  （按空间，只存和默认不同的），键是 `platform:<技能 id>` 或 `bundled:<名字>`；收窄时选中的对象存成本空间对
  resource_of(键) 的「使用」授权，判定照常走权限模块（管理员本来就能用全部）；
- 会话清单按人编：停用了的、收窄后这个人用不了的都不给；宿主拉清单时拿到这个人用不了的内置技能，不注册它们；
- 企业技能删掉后各空间里指向它的设置与授权留着无害（id 不会再用），不跨空间去清。
"""

import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.errors import AccessError
from ..access.rls import set_workspace
from ..access.vocab import InvalidInput
from ..access.workspaces import PLATFORM_WORKSPACE_ID
from .bundled import BundledSkills
from .errors import PackageInvalid, SkillForbidden
from .models import Skill, SkillWorkspaceSetting
from .service import EVERYONE, AccessChecker
from .views import Actor

ALL_WORKSPACES = "*"
PLATFORM = "platform:"
BUNDLED = "bundled:"
KEY = re.compile(r"^(platform:[A-Za-z0-9_.\-]{1,64}|bundled:[a-z0-9]+(?:-[a-z0-9]+)*)$")


@dataclass(frozen=True)
class Setting:
    """空间管理员对一个上层技能的设置；没有记录就是默认（启用、全体成员）。"""

    off: bool = False
    narrowed: bool = False


DEFAULT = Setting()


def resource_of(key: str) -> str:
    """收窄时授权挂在哪个资源上：企业技能用它的 id（全局唯一，和空间技能不撞），内置技能用 bundled.<名字>。"""
    if key.startswith(PLATFORM):
        return f"skill:{key[len(PLATFORM):]}"
    return f"skill:bundled.{key[len(BUNDLED):]}"


def usable(access: AccessChecker, workspace_id: str, user_id: str, key: str, setting: Setting) -> bool:
    """这个人在这个空间能不能用这个上层技能。"""
    if setting.off:
        return False
    return not setting.narrowed or access.check(user_id, workspace_id, resource_of(key), "use")


def published_description(row: Skill) -> str:
    """对外列出时用发布版本的说明（row.description 跟着草稿变）。"""
    return str((row.published_manifest or {}).get("description") or row.description)


def reaches(audience: list | None, workspace_id: str) -> bool:
    return ALL_WORKSPACES in (audience or ()) or workspace_id in (audience or ())


async def platform_rows(sessionmaker: async_sessionmaker, workspace_id: str) -> list[Skill]:
    """发到这个空间、已发布、已启用的企业技能（不管这个空间停没停用），按名字排。"""
    async with sessionmaker() as session, session.begin():
        await set_workspace(session, PLATFORM_WORKSPACE_ID)
        rows = (await session.scalars(select(Skill).where(
            Skill.workspace_id == PLATFORM_WORKSPACE_ID, Skill.scope_key == "platform", Skill.enabled.is_(True),
            Skill.status == "published").order_by(Skill.name))).all()
    return [row for row in rows if reaches(row.audience, workspace_id)]


async def settings_of(sessionmaker: async_sessionmaker, workspace_id: str) -> dict[str, Setting]:
    """本空间对上层技能的设置（键 → 设置），没列出的都是默认。"""
    async with sessionmaker() as session, session.begin():
        await set_workspace(session, workspace_id)
        rows = (await session.scalars(select(SkillWorkspaceSetting).where(
            SkillWorkspaceSetting.workspace_id == workspace_id))).all()
    return {row.skill_key: Setting(off=row.off, narrowed=row.narrowed) for row in rows}


@dataclass(frozen=True)
class UpperSkill:
    """空间管理员「企业」页签的一行。resource 是收窄时授权挂的资源（界面按它从授权列表里找出给了谁）。"""

    key: str
    name: str
    description: str
    source: str  # platform | bundled
    version: str
    off: bool
    narrowed: bool

    @property
    def resource(self) -> str:
        return resource_of(self.key)

    def to_json(self) -> dict[str, Any]:
        return {"key": self.key, "name": self.name, "description": self.description, "source": self.source,
                "version": self.version, "off": self.off, "narrowed": self.narrowed, "resource": self.resource}


class UpperSkills:
    def __init__(self, sessionmaker: async_sessionmaker, access: AccessChecker, bundled: BundledSkills) -> None:
        self._sessionmaker = sessionmaker
        self._access = access
        self._bundled = bundled

    def _admin(self, actor: Actor) -> None:
        if not self._access.check(actor.user_id, actor.workspace_id, "workspace", "use") or \
                not self._access.visible_resources(actor.user_id, actor.workspace_id, "skill", "owner").all_:
            raise SkillForbidden("只有空间管理员可以在本空间停用企业技能、改给谁用")

    @asynccontextmanager
    async def _tx(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    async def list(self, actor: Actor) -> tuple[UpperSkill, ...]:
        """到达本空间的上层技能：企业技能在前，内置的在后；同名时企业技能优先（内置那份不列）。"""
        self._admin(actor)
        settings = await settings_of(self._sessionmaker, actor.workspace_id)

        def upper(key: str, name: str, description: str, source: str, version: str) -> UpperSkill:
            setting = settings.get(key, DEFAULT)
            return UpperSkill(key=key, name=name, description=description, source=source, version=version,
                              off=setting.off, narrowed=setting.narrowed)

        platform = [upper(f"{PLATFORM}{row.id}", row.name, published_description(row), "platform",
                          row.published_version or "")
                    for row in await platform_rows(self._sessionmaker, actor.workspace_id)]
        taken = {skill.name for skill in platform}
        bundled = [upper(f"{BUNDLED}{skill.name}", skill.name, skill.description, "bundled", "")
                   for skill in await self._bundled.list() if skill.name not in taken]
        return (*platform, *bundled)

    async def set_off(self, actor: Actor, key: str, off: bool) -> None:
        self._admin(actor)
        _check_key(key)
        await self._update(actor, key, off=off)

    async def set_users(self, actor: Actor, key: str, subjects: Sequence[str]) -> bool:
        """改给谁用：选了「全体成员」就是不收窄（不看授权），否则收窄到选中的对象（一个都不选就只有管理员）。
        授权与标记分两步写，顺序保证中途失败也不会放大：收窄时先写授权再立标记，放开时先撤标记再清授权。
        返回现在是否收窄。"""
        self._admin(actor)
        _check_key(key)
        narrowed = EVERYONE not in subjects
        if narrowed:
            await self._grant(actor, key, subjects)
            await self._update(actor, key, narrowed=True)
        else:
            await self._update(actor, key, narrowed=False)
            await self._grant(actor, key, ())
        return narrowed

    async def _grant(self, actor: Actor, key: str, subjects: Sequence[str]) -> None:
        try:
            await self._access.set_users(actor.user_id, actor.workspace_id, resource_of(key), subjects)
        except (AccessError, InvalidInput) as error:  # 选的对象不存在、格式不对
            raise PackageInvalid("invalid_subjects", str(error), field="subjects") from error

    async def _update(self, actor: Actor, key: str, **changes: bool) -> None:
        """改一项设置；改完和默认一样就删掉那一行。"""
        async with self._tx(actor.workspace_id) as session:
            row = await session.get(SkillWorkspaceSetting, (actor.workspace_id, key), with_for_update=True)
            current = Setting(off=row.off, narrowed=row.narrowed) if row is not None else DEFAULT
            wanted = Setting(**{"off": current.off, "narrowed": current.narrowed, **changes})
            if wanted == DEFAULT:
                if row is not None:
                    await session.execute(delete(SkillWorkspaceSetting).where(
                        SkillWorkspaceSetting.workspace_id == actor.workspace_id,
                        SkillWorkspaceSetting.skill_key == key))
            elif row is None:
                session.add(SkillWorkspaceSetting(workspace_id=actor.workspace_id, skill_key=key, off=wanted.off,
                                                  narrowed=wanted.narrowed, updated_by=actor.user_id))
            else:
                row.off, row.narrowed, row.updated_by, row.updated_at = (wanted.off, wanted.narrowed, actor.user_id,
                                                                         func.now())


def _check_key(key: str) -> None:
    if not KEY.fullmatch(key):
        raise PackageInvalid("invalid_key", "要设置的技能不对", field="key")
