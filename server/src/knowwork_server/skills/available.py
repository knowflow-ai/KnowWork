"""某人在对话里能用的技能：本会话清单（企业、空间、个人）加上宿主内置的技能。「技能」页的「可以用的」与 @ 菜单都用它。

内置技能这个人在本空间用不了的（停用了、收窄后不在内）不列；和会话清单里的技能同名的也不列（宿主那边同名时只给会话清单里那份）。
"""

from .bundled import BundledSkills
from .catalog import SkillCatalog
from .views import AvailableSkill

SOURCE_ORDER = {"platform": 0, "bundled": 1, "workspace": 2, "personal": 3}


class SkillAvailability:
    def __init__(self, catalog: SkillCatalog, bundled: BundledSkills) -> None:
        self._catalog = catalog
        self._bundled = bundled

    async def for_user(self, workspace_id: str, user_id: str) -> tuple[AvailableSkill, ...]:
        entries = await self._catalog.for_session(workspace_id, user_id)
        own = [AvailableSkill(name=e.name, description=e.description, source=e.scope, version=e.version,
                              user_invocable=e.user_invocable) for e in entries]
        hidden = {skill.name for skill in own} | set(await self._catalog.bundled_off(workspace_id, user_id))
        bundled = [skill for skill in await self._bundled.list() if skill.name not in hidden]
        return tuple(sorted((*own, *bundled), key=lambda s: (SOURCE_ORDER.get(s.source, 9), s.name)))
