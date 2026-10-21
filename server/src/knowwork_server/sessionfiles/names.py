"""文件名：清洗用户与模型给出的名字、会话内去重、下载时的 Content-Disposition。"""

import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import quote

MAX_NAME_LENGTH = 120
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_UNSAFE = re.compile(r'[<>:"|?*]')


class InvalidFileName(ValueError):
    pass


def _cut(name: str, limit: int) -> str:
    if len(name) <= limit:
        return name
    stem, dot, ext = name.rpartition(".")
    if dot and stem and len(ext) <= 16:
        return stem[: limit - len(ext) - 1] + "." + ext
    return name[:limit]


def clean_name(raw: str | None) -> str:
    """去掉路径部分与控制字符，统一成 NFC；不合法（空、只有点、以点开头）一律拒绝。"""
    if not raw:
        raise InvalidFileName("文件名为空")
    base = re.split(r"[\\/]", raw)[-1]
    base = unicodedata.normalize("NFC", _UNSAFE.sub("_", _CONTROL.sub("", base))).strip()
    if not base or base.startswith(".") or set(base) <= {"."}:
        raise InvalidFileName("文件名不合法")
    return _cut(base, MAX_NAME_LENGTH)


def is_plain_name(name: str) -> bool:
    """沙箱 output 顶层文件名是否可以原样当产物名：不含分隔符、控制字符，不以点开头。"""
    try:
        return clean_name(name) == name
    except InvalidFileName:
        return False


def extension(name: str) -> str:
    stem, dot, ext = name.rpartition(".")
    return ext.lower() if dot and stem else ""


def replace_extension(name: str, ext: str) -> str:
    stem, dot, _ = name.rpartition(".")
    return f"{stem if dot and stem else name}.{ext}"


def unique_name(name: str, taken: Iterable[str]) -> str:
    """会话内重名时追加「 (2)」「 (3)」……；比较不区分大小写，避免大小写不敏感的文件系统上撞名。"""
    used = {item.casefold() for item in taken}
    if name.casefold() not in used:
        return name
    stem, dot, ext = name.rpartition(".")
    if not (dot and stem):
        stem, ext = name, ""
    for index in range(2, 10_000):
        candidate = f"{stem} ({index})" + (f".{ext}" if ext else "")
        if candidate.casefold() not in used:
            return candidate
    raise InvalidFileName("同名文件过多")


def content_disposition(name: str) -> str:
    """一律 attachment：ASCII 兜底名给老客户端，filename* 按 RFC 6266/5987 携带 UTF-8 原名。"""
    fallback = "".join(ch if 0x20 <= ord(ch) < 0x7f and ch not in '"\\;' else "_" for ch in name) or "download"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"
