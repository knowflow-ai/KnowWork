"""系统信息：KnowWork 版本、DSH 版本、数据库迁移版本、授权与扩展模块。给系统管理员排查问题、报问题时用。

取不到的一项写「未知」，不让整页报错：宿主没起来时 DSH 版本取不到，页面其余部分照样能看。
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..extensions import NO_EXTENSIONS, Extensions

log = logging.getLogger(__name__)

DshVersion = Callable[[], Awaitable[str | None]]


@dataclass(frozen=True)
class ExtensionInfo:
    name: str
    enabled: bool
    reason: str | None = None  # 没启用的原因


@dataclass(frozen=True)
class SystemInfo:
    knowwork: str
    dsh: str | None
    database: str | None
    license: str = NO_EXTENSIONS.license.message
    extensions: tuple[ExtensionInfo, ...] = ()


class SystemInfoService:
    def __init__(self, version: str, sessionmaker: async_sessionmaker, dsh_version: DshVersion,
                 extensions: Extensions = NO_EXTENSIONS) -> None:
        self._version = version
        self._sessionmaker = sessionmaker
        self._dsh_version = dsh_version
        self._extensions = extensions

    async def info(self) -> SystemInfo:
        enabled = tuple(ExtensionInfo(e.name, True) for e in self._extensions.active)
        skipped = tuple(ExtensionInfo(s.name, False, s.reason) for s in self._extensions.skipped)
        return SystemInfo(knowwork=self._version, dsh=await self._dsh(), database=await self._database(),
                          license=self._extensions.license.message, extensions=enabled + skipped)

    async def _dsh(self) -> str | None:
        try:
            return await self._dsh_version()
        except Exception:  # 宿主不通只影响这一项，原因进日志
            log.warning("reading DSH version from host failed", exc_info=True)
            return None

    async def _database(self) -> str | None:
        try:
            async with self._sessionmaker() as session:
                # 装了扩展时每条迁移分支一行
                return await session.scalar(text(
                    "SELECT string_agg(version_num, ', ' ORDER BY version_num) FROM alembic_version"))
        except Exception:  # 用 create_all 建的库（测试、本机临时库）没有迁移记录
            log.warning("reading alembic version failed", exc_info=True)
            return None
