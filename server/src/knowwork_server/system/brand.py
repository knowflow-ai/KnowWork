"""品牌：名称与 Logo 的读取和修改。只有系统管理员（初始化时建的那个账号）能改。

Logo 只收常见图片：PNG、JPEG、WebP、SVG，按文件头核对类型，不信上传时声明的类型。
SVG 能带脚本，除了这里拒掉脚本、事件属性、外部实体，下载接口还加了 CSP sandbox，直接打开也执行不了。
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import SINGLETON_ID, SystemBrand

DEFAULT_NAME = "KnowWork"
MAX_NAME_LENGTH = 40
MAX_LOGO_BYTES = 512 * 1024

# SVG 里不许出现的东西：脚本、事件属性、外链脚本、内嵌 HTML、DOCTYPE 与实体（防实体膨胀）
_SVG_FORBIDDEN = re.compile(
    rb"<\s*script|<\s*foreignobject|<!doctype|<!entity|\son[a-z]+\s*=|javascript:|data:text/html",
    re.IGNORECASE,
)
_SVG_ROOT = re.compile(rb"^\s*(<\?xml[^>]*\?>\s*)?(<!--.*?-->\s*)*<svg[\s>]", re.IGNORECASE | re.DOTALL)


class Forbidden(Exception):
    """不是系统管理员。"""


class InvalidBrand(ValueError):
    """名称或 Logo 不合要求，消息直接给用户看。"""


@dataclass(frozen=True)
class Brand:
    name: str
    # Logo 的版本号（修改时间戳），为空表示用默认 Logo；前端拼进地址，换了 Logo 浏览器就不用旧缓存
    logo_version: str | None


IsSystemAdmin = Callable[[str], Awaitable[bool]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def logo_type_of(data: bytes) -> str:
    """按文件头认出图片类型；不是支持的类型就报错。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    head = data[:4096]
    if _SVG_ROOT.match(head):
        if _SVG_FORBIDDEN.search(data):
            raise InvalidBrand("SVG 里不能带脚本、事件或外部内容，请导出成普通 SVG 或 PNG")
        return "image/svg+xml"
    raise InvalidBrand("Logo 只支持 PNG、JPEG、WebP 或 SVG")


def clean_name(name: str | None) -> str | None:
    """名称去掉首尾空白；空表示恢复默认。"""
    value = (name or "").strip()
    if not value:
        return None
    if len(value) > MAX_NAME_LENGTH:
        raise InvalidBrand(f"名称不能超过 {MAX_NAME_LENGTH} 个字")
    if any(ch in value for ch in "<>\n\r\t"):
        raise InvalidBrand("名称里不能有尖括号或换行")
    return value


class BrandService:
    def __init__(self, sessionmaker: async_sessionmaker, is_system_admin: IsSystemAdmin,
                 clock: Callable[[], datetime] = _utcnow) -> None:
        self._sessionmaker = sessionmaker
        self._is_system_admin = is_system_admin
        self._clock = clock

    async def is_admin(self, actor: str) -> bool:
        return await self._is_system_admin(actor)

    async def brand(self) -> Brand:
        async with self._sessionmaker() as session:
            row = await session.get(SystemBrand, SINGLETON_ID)
        return _brand_of(row)

    async def logo(self) -> tuple[bytes, str] | None:
        async with self._sessionmaker() as session:
            row = await session.get(SystemBrand, SINGLETON_ID)
        if row is None or row.logo is None or row.logo_type is None:
            return None
        return row.logo, row.logo_type

    async def rename(self, actor: str, name: str | None) -> Brand:
        await self._require(actor)
        return await self._save(actor, name=clean_name(name))

    async def set_logo(self, actor: str, data: bytes) -> Brand:
        await self._require(actor)
        if not data:
            raise InvalidBrand("没有收到图片")
        if len(data) > MAX_LOGO_BYTES:
            raise InvalidBrand("Logo 不能超过 512 KB")
        return await self._save(actor, logo=(data, logo_type_of(data)))

    async def clear_logo(self, actor: str) -> Brand:
        await self._require(actor)
        return await self._save(actor, logo=(None, None))

    async def _require(self, actor: str) -> None:
        if not await self._is_system_admin(actor):
            raise Forbidden()

    async def _save(self, actor: str, **changes: object) -> Brand:
        values: dict[str, object] = {"updated_at": self._clock(), "updated_by": actor}
        if "name" in changes:
            values["name"] = changes["name"]
        if "logo" in changes:
            data, kind = changes["logo"]  # type: ignore[misc]
            values["logo"], values["logo_type"] = data, kind
        statement = insert(SystemBrand).values(id=SINGLETON_ID, **values)
        statement = statement.on_conflict_do_update(index_elements=[SystemBrand.id], set_=values)
        async with self._sessionmaker() as session, session.begin():
            await session.execute(statement)
        return await self.brand()


def _brand_of(row: SystemBrand | None) -> Brand:
    if row is None:
        return Brand(name=DEFAULT_NAME, logo_version=None)
    version = str(int(row.updated_at.timestamp() * 1000)) if row.logo is not None and row.updated_at else None
    return Brand(name=row.name or DEFAULT_NAME, logo_version=version)
