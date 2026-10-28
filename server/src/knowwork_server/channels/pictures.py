"""回答里的图：把 [IMG:n:j] 换成真正的图片，交给平台适配器随最后一帧发出。

图的地址在这一轮的引用目录里（第 n 条出处的第 j 张图，知识源给的站内取图地址）。不发外链：内网部署时员工手机
打不开 KnowWork 的地址（WeKnora 发的是 2 小时有效的公开签名链接，要求外部能访问）。这里给渠道账号签一个取图
链接，在进程内调业务服务自己的取图接口把图取回来，再由适配器传到平台上；取图接口照常按签名里的用户判权，
机器人只发得出它能查的知识库里的图。哪个知识源的图都走同一条路，渠道不用认识知识源。
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

from ..knowledge.citations import IMAGE_MARK
from .types import Picture

log = logging.getLogger(__name__)

MAX_PICTURES = 10  # 企业微信一条最多 10 张，三家统一
MAX_BYTES = 10 * 1024 * 1024
FETCH_TIMEOUT = 20.0


class Catalogs(Protocol):
    async def load(self, workspace_id: str, catalog_id: str) -> Any: ...


Sign = Callable[[str, str], str]  # (站内地址, 用户) → 带签名的地址
Fetch = Callable[[str], Awaitable[tuple[bytes, str] | None]]  # 带签名的站内地址 → (内容, 类型)


class InProcessFetch:
    """在进程内调业务服务自己的接口取图（不走网络、不用知道对外地址）。app 建好后再接上。"""

    def __init__(self) -> None:
        self._app: Any = None

    def bind(self, app: Any) -> None:
        self._app = app

    async def __call__(self, path: str) -> tuple[bytes, str] | None:
        if self._app is None:
            return None
        transport = httpx.ASGITransport(app=self._app)
        async with httpx.AsyncClient(transport=transport, base_url="http://knowwork.internal",
                                     timeout=FETCH_TIMEOUT) as client:
            response = await client.get(path)
        mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
        if response.status_code != 200 or not mime.startswith("image/") or len(response.content) > MAX_BYTES:
            log.info("channel picture skipped: %s %s %d bytes", response.status_code, mime, len(response.content))
            return None
        return response.content, mime


class Pictures:
    def __init__(self, catalogs: Catalogs, sign: Sign, fetch: Fetch) -> None:
        self._catalogs = catalogs
        self._sign = sign
        self._fetch = fetch

    async def of(self, workspace_id: str, conversation_id: str, user_id: str, answer: str) -> list[Picture]:
        """回答里用到的图（按出现先后、去重，最多 MAX_PICTURES 张）；取不到的跳过。"""
        marks = list(dict.fromkeys((int(m.group(1)), int(m.group(2))) for m in IMAGE_MARK.finditer(answer)))
        if not marks:
            return []
        catalog = await self._catalogs.load(workspace_id, conversation_id)
        found: list[Picture] = []
        for number, index in marks:
            if len(found) >= MAX_PICTURES:
                break
            entry = catalog.lookup(number)
            images = entry.evidence.images if entry is not None else ()
            if index >= len(images) or not images[index].url:
                continue
            try:
                got = await self._fetch(self._sign(images[index].url, user_id))
            except Exception:  # 一张图取不到不影响回答
                log.warning("channel picture fetch failed", exc_info=True)
                continue
            if got is not None:
                found.append(Picture(data=got[0], mime=got[1], caption=images[index].caption))
        return found
