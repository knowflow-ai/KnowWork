"""Office 容器看到的文件放在哪：两种实现同一组方法（ready、place、fetch、mark、copies）。
- 按人的 Office 容器（containers.py 的 OfficeContainers）：放进这个人容器的 /work/<对话>/...；
- 本机开发（没设镜像、连一个固定的、从源码起的 GenOffice 网页模式）：放进本机目录 files_root/<对话>/...，
  GenOffice 和业务服务在同一台机器上，看到的是同一个路径。所有人共用这一个，只用于开发；它的 MCP 地址由配置给
  （KNOWWORK_OFFICE_UPSTREAM_MCP_URL，本机 GenOffice 不带门卫）。
两种都给出这段对话的挂载点（mount）与这个人 GenOffice 的 MCP（mcp，容器没在跑是 None）：智能体经编辑器改文档经它。
也都在文件根下的 .knowwork 里记每份「和对话一致」时的摘要（放进去时一并记，mark 更新，copies 连同文件此刻的摘要一起读，
不把文件搬出来）：在线编辑在打开时、一轮结束查改动时用。
"""

import asyncio
import hashlib
from pathlib import Path
from typing import Protocol

from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.tools.mcp.client import Endpoint
from knowwork_server.workpath import Mount, WorkPath

from .containers import SYNC_MAX_BYTES, OfficeCopy, OfficeKey, office_mount, sync_record, sync_value


class OfficeFiles(Protocol):
    def mount(self, conversation_id: str) -> Mount: ...

    async def mcp(self, key: OfficeKey) -> Endpoint | None: ...

    async def ready(self, key: OfficeKey) -> None: ...

    async def place(self, key: OfficeKey, conversation_id: str, path: WorkPath, data: bytes) -> str: ...

    async def fetch(self, key: OfficeKey, conversation_id: str, path: WorkPath, *, max_bytes: int) -> bytes | None: ...

    async def mark(self, key: OfficeKey, conversation_id: str, path: WorkPath, value: str) -> None: ...

    async def copies(self, key: OfficeKey, conversation_id: str,
                     paths: list[WorkPath]) -> dict[str, OfficeCopy] | None: ...


class LocalOfficeFiles:
    def __init__(self, root: Path, mcp_url: str | None = None) -> None:
        self._root = root.resolve()
        self._mcp_url = mcp_url

    def mount(self, conversation_id: str) -> Mount:
        office_mount(conversation_id)  # 对话标识照同一个规矩校验
        return Mount(str(self._root / conversation_id))

    async def mcp(self, key: OfficeKey) -> Endpoint | None:
        return Endpoint(self._mcp_url, timeout_seconds=120.0) if self._mcp_url else None

    def _path(self, conversation_id: str, work: WorkPath) -> Path:
        """本机开发目录里的挂载点：files_root/<对话>，同编辑器容器的 /work/<对话>（office_mount）。"""
        if work.is_root:
            raise SandboxError("文件路径不对")
        path = Path(self.mount(conversation_id).at(work)).resolve()
        if not path.is_relative_to(self._root):  # 符号链接按真实位置判
            raise SandboxError("文件路径不对")
        return path

    async def ready(self, key: OfficeKey) -> None:
        return None

    def _record(self, conversation_id: str, work: WorkPath) -> Path:
        path = (self._root / sync_record(conversation_id, work)).resolve()
        if not path.is_relative_to(self._root):
            raise SandboxError("文件路径不对")
        return path

    async def place(self, key: OfficeKey, conversation_id: str, work: WorkPath, data: bytes) -> str:
        path = self._path(conversation_id, work)
        record = self._record(conversation_id, work)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(sync_value(data))

        await asyncio.to_thread(write)
        return str(path)

    async def mark(self, key: OfficeKey, conversation_id: str, work: WorkPath, value: str) -> None:
        record = self._record(conversation_id, work)

        def write() -> None:
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(value)

        await asyncio.to_thread(write)

    async def copies(self, key: OfficeKey, conversation_id: str,
                     paths: list[WorkPath]) -> dict[str, OfficeCopy] | None:
        def look() -> dict[str, OfficeCopy]:
            found: dict[str, OfficeCopy] = {}
            for work in paths:
                path = self._path(conversation_id, work)
                digest = None
                if path.is_file():
                    with path.open("rb") as handle:
                        digest = hashlib.file_digest(handle, "sha256").hexdigest()
                found[work.relative] = OfficeCopy(digest=digest, synced=_record_value(self._record(conversation_id, work)))
            return found

        return await asyncio.to_thread(look)

    async def fetch(self, key: OfficeKey, conversation_id: str, work: WorkPath, *, max_bytes: int) -> bytes | None:
        path = self._path(conversation_id, work)

        def read() -> bytes | None:
            if not path.is_file():
                return None
            if path.stat().st_size > max_bytes:
                raise SandboxError("文件太大")
            return path.read_bytes()

        return await asyncio.to_thread(read)


def _record_value(record: Path) -> str | None:
    if not record.is_file() or record.stat().st_size > SYNC_MAX_BYTES:
        return None
    return record.read_text().strip() or None
