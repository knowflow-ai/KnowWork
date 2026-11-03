"""平台要求放在域名根目录的校验文件：企业微信验证可信域名用的 WW_verify_xxx.txt（以后别的平台同理）。

网关把根目录下的这类文件转到 /api/auth/verify-files/{name}（deploy/gateway/nginx.conf）；内容由扩展在管理后台上传，
按文件名查。没有扩展登记、或没有这个文件时 404。文件名只认字母、数字、下划线与横线加 .txt，别的直接当没有。
"""

import logging
import re
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)
FILE_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,96}\.txt$")
Lookup = Callable[[str], Awaitable[str | None]]


class VerifyFiles:
    def __init__(self) -> None:
        self._lookups: list[Lookup] = []

    def add(self, lookup: Lookup) -> None:
        self._lookups.append(lookup)

    async def find(self, name: str) -> str | None:
        if not FILE_NAME.match(name):
            return None
        for lookup in self._lookups:
            try:
                content = await lookup(name)
            except Exception:
                log.exception("verify file lookup failed")
                continue
            if content is not None:
                return content
        return None
