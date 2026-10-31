"""执行命令时要带上的技能环境变量（做法见 env.py）：宿主报上这个会话加载过的技能，按此刻的会话清单核对后取值。"""

from sqlalchemy import select

from ..access.rls import set_workspace
from ..access.workspaces import PLATFORM_WORKSPACE_ID
from .catalog import SkillCatalog
from .env import EnvBox
from .manifest import ENV_NAME, RESERVED_ENV
from .models import Skill


class SkillEnvironment:
    """执行命令时要带上的环境变量。"""

    def __init__(self, sessionmaker, catalog: SkillCatalog, box: EnvBox) -> None:
        self._sessionmaker = sessionmaker
        self._catalog = catalog
        self._box = box

    async def for_exec(self, workspace_id: str, user_id: str, loaded: list[str]) -> dict[str, str]:
        wanted = set(loaded)
        entries = [e for e in await self._catalog.for_session(workspace_id, user_id)
                   if e.name in wanted and e.env]
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
