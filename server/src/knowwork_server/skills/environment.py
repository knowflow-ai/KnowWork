"""执行命令时要带上的技能环境变量（做法见 env.py）：宿主报上这个会话加载过的技能，按此刻的会话清单核对后取值。

两处用：云端对话在沙箱执行接口里加进命令（for_exec，值不经过宿主）；用户电脑上的本机会话由本机宿主来取、加进本机命令
（for_device）。本机只给个人技能的值：企业技能、空间技能的值是管理员填的（公司的凭据），下发到用户电脑上用户就能读到原文，
所以不出服务器；这类技能在本机会话的技能清单里注明用不了（internal_api）。"""

from sqlalchemy import select

from ..access.rls import set_workspace
from ..access.workspaces import PLATFORM_WORKSPACE_ID
from .catalog import SkillCatalog
from .env import EnvBox
from .manifest import ENV_NAME, RESERVED_ENV
from .models import Skill

PERSONAL = "personal"


class SkillEnvironment:
    """执行命令时要带上的环境变量。"""

    def __init__(self, sessionmaker, catalog: SkillCatalog, box: EnvBox) -> None:
        self._sessionmaker = sessionmaker
        self._catalog = catalog
        self._box = box

    async def for_exec(self, workspace_id: str, user_id: str, loaded: list[str]) -> dict[str, str]:
        return await self._values(workspace_id, user_id, loaded, personal_only=False)

    async def for_device(self, workspace_id: str, user_id: str, loaded: list[str]) -> dict[str, str]:
        """本机会话要的：只给个人技能的值（理由见模块说明）。"""
        return await self._values(workspace_id, user_id, loaded, personal_only=True)

    async def _values(self, workspace_id: str, user_id: str, loaded: list[str], *,
                      personal_only: bool) -> dict[str, str]:
        wanted = set(loaded)
        entries = [e for e in await self._catalog.for_session(workspace_id, user_id)
                   if e.name in wanted and e.env and (not personal_only or e.scope == PERSONAL)]
        if not entries:
            return {}
        rows = {**await self._rows(PLATFORM_WORKSPACE_ID, [e.skill_id for e in entries if e.scope == "platform"]),
                **await self._rows(workspace_id, [e.skill_id for e in entries if e.scope != "platform"])}
        values: dict[str, str] = {}
        for entry in entries:  # 清单顺序就是上层在前
            row = rows.get(entry.skill_id)
            secrets = self._box.read(row) if row is not None else {}
            for name in entry.env:
                if name in secrets and name not in RESERVED_ENV and ENV_NAME.match(name):
                    values.setdefault(name, secrets[name])
        return values

    async def _rows(self, workspace_id: str, ids: list[str]) -> dict[str, Skill]:
        if not ids:
            return {}
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            rows = (await session.scalars(select(Skill).where(Skill.workspace_id == workspace_id,
                                                              Skill.id.in_(ids)))).all()
        return {row.id: row for row in rows}
