"""回合结束时收产物：模型用宿主的官方 present 交付的文件（技术方案 9.1、9.2）。

collect(workspace_id, session_id, paths=...) 供事件观察者在回合完成时调用，paths 是本回合 present 过的路径
（宿主路径 /sessions/<会话>/...）：
- 只收本会话工作目录里的普通文件；同一回合同名的文件只收第一个，其余说明原因；
- 上一轮已经收过且内容没变的同名文件跳过，不重复计数；
- 上限：单文件 20 MiB、每轮 20 个、单会话累计 100 MiB；超出、读不出、表格无法检查的文件逐项写进
  notices，并拼成一条可直接展示的 message；
- xlsx/csv/tsv 存储前做公式转义；含宏或旧格式的表格（xlsm、xls、xlsb、ods）无法转义，不收。
"""

import asyncio
import hashlib
import logging
import mimetypes
import posixpath
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..access.rls import set_workspace
from ..sandbox.contract import SandboxError, SandboxTooLarge, SessionSandbox
from ..sandbox.paths import InvalidPath, from_session_path
from ..sessionfiles.names import extension, is_plain_name
from ..sessionfiles.storage import SessionStores
from ..skills.errors import BlobIntegrityError
from .models import Artifact
from .sanitize import SpreadsheetRejected, sanitize_csv, sanitize_xlsx

log = logging.getLogger(__name__)
MIB = 1024 * 1024
UNSANITIZABLE = frozenset({"xlsm", "xls", "xlsb", "ods", "xltm"})
CONTENT_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "md": "text/markdown; charset=utf-8", "csv": "text/csv; charset=utf-8", "txt": "text/plain; charset=utf-8",
}


@dataclass(frozen=True)
class ArtifactLimits:
    max_file_bytes: int = 20 * MIB
    max_per_turn: int = 20
    max_session_bytes: int = 100 * MIB

DEFAULT_LIMITS = ArtifactLimits()


@dataclass(frozen=True)
class Notice:
    filename: str
    code: str
    message: str


@dataclass(frozen=True)
class ArtifactView:
    id: str
    filename: str
    content_type: str
    size: int
    run_id: str | None
    sanitized: bool
    created_at: datetime | None


@dataclass(frozen=True)
class CollectResult:
    artifacts: tuple[ArtifactView, ...] = ()
    notices: tuple[Notice, ...] = ()

    @property
    def message(self) -> str | None:
        """面向用户的一条说明；全部收下时为 None。"""
        if not self.notices:
            return None
        lines = [f"- {n.filename}：{n.message}" if n.filename else f"- {n.message}" for n in self.notices]
        return f"有 {len(self.notices)} 个文件没有收为产物：\n" + "\n".join(lines)


def artifact_view(row: Artifact) -> ArtifactView:
    return ArtifactView(id=row.id, filename=row.filename, content_type=row.content_type, size=row.size,
                        run_id=row.run_id, sanitized=row.sanitized, created_at=row.created_at)


def content_type_of(name: str) -> str:
    ext = extension(name)
    return CONTENT_TYPES.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


async def sanitize(name: str, data: bytes) -> tuple[bytes, bool]:
    ext = extension(name)
    if ext == "xlsx":
        return await asyncio.to_thread(sanitize_xlsx, data)
    if ext in ("csv", "tsv"):
        return await asyncio.to_thread(sanitize_csv, data, delimiter="\t" if ext == "tsv" else ",")
    return data, False


@dataclass
class _Budget:
    """一次收集的计数器，只在 collect 内部使用。"""

    used_bytes: int
    collected: int = 0
    seen: set[tuple[str, str]] = field(default_factory=set)


class ArtifactCollector:
    def __init__(self, sessionmaker: async_sessionmaker, stores: SessionStores, sandbox: SessionSandbox,
                 limits: ArtifactLimits = DEFAULT_LIMITS) -> None:
        self._sessionmaker = sessionmaker
        self._stores = stores
        self._sandbox = sandbox
        self._limits = limits

    async def collect(self, workspace_id: str, session_id: str, *, run_id: str | None = None,
                      paths: Sequence[str] = ()) -> CollectResult:
        if not paths:
            return CollectResult()
        budget = await self._budget(workspace_id, session_id)
        artifacts, notices, names = [], [], set()
        for path in dict.fromkeys(paths):  # 同一路径交付多次只收一次
            name = posixpath.basename(path)
            if name in names:
                notices.append(Notice(name, "duplicate_name", "本轮已经交付过同名文件"))
                continue
            names.add(name)
            outcome = await self._one(workspace_id, session_id, run_id, path, name, budget)
            if isinstance(outcome, Notice):
                notices.append(outcome)
            elif outcome is not None:
                artifacts.append(outcome)
        return CollectResult(artifacts=tuple(artifacts), notices=tuple(notices))

    async def _budget(self, workspace_id: str, session_id: str) -> _Budget:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            used = await session.scalar(select(func.coalesce(func.sum(Artifact.size), 0))
                                        .where(Artifact.conversation_id == session_id))
            seen = (await session.execute(select(Artifact.filename, Artifact.source_sha256)
                                          .where(Artifact.conversation_id == session_id))).all()
        return _Budget(used_bytes=int(used), seen={(name, digest) for name, digest in seen})

    @staticmethod
    def _precheck(name: str) -> Notice | None:
        if not is_plain_name(name):
            return Notice(name, "invalid_name", "文件名不合法")
        if extension(name) in UNSANITIZABLE:
            return Notice(name, "unsupported_spreadsheet", "含宏或旧格式的表格无法做安全检查，请输出 xlsx 或 csv")
        return None

    async def _read(self, workspace_id: str, session_id: str, path: str, name: str) -> bytes | Notice:
        try:
            container_path = from_session_path(path, session_id)
        except InvalidPath:
            return Notice(name, "outside_workspace", "只能交付会话工作目录里的文件")
        try:
            return await self._sandbox.read_delivered(workspace_id, session_id, container_path,
                                                      max_bytes=self._limits.max_file_bytes)
        except SandboxTooLarge:
            return Notice(name, "file_too_large", f"超过单文件 {self._limits.max_file_bytes // MIB} MiB 上限")
        except SandboxError as error:
            return Notice(name, "read_failed", f"读取失败（{error}）")

    async def _one(self, workspace_id: str, session_id: str, run_id: str | None, path: str, name: str,
                   budget: _Budget) -> ArtifactView | Notice | None:
        notice = self._precheck(name)
        if notice is not None:
            return notice
        raw = await self._read(workspace_id, session_id, path, name)
        if isinstance(raw, Notice):
            return raw
        source = hashlib.sha256(raw).hexdigest()
        if (name, source) in budget.seen:
            return None  # 之前收过、内容没变
        try:
            content, sanitized = await sanitize(name, raw)
        except SpreadsheetRejected as error:
            return Notice(name, "spreadsheet_rejected", str(error))
        quota = self._quota(name, len(content), budget)
        if quota is not None:
            return quota
        row = await self._store(workspace_id, session_id, run_id, name, content, source, sanitized)
        if isinstance(row, Notice):
            return row
        budget.collected += 1
        budget.used_bytes += len(content)
        budget.seen.add((name, source))
        return row

    def _quota(self, name: str, size: int, budget: _Budget) -> Notice | None:
        if size > self._limits.max_file_bytes:
            return Notice(name, "file_too_large", f"超过单文件 {self._limits.max_file_bytes // MIB} MiB 上限")
        if budget.collected >= self._limits.max_per_turn:
            return Notice(name, "turn_limit", f"超过每轮最多 {self._limits.max_per_turn} 个产物的上限")
        if budget.used_bytes + size > self._limits.max_session_bytes:
            return Notice(name, "session_quota", f"本会话产物累计将超过 {self._limits.max_session_bytes // MIB} MiB 上限")
        return None

    async def _store(self, workspace_id: str, session_id: str, run_id: str | None, name: str, content: bytes,
                     source: str, sanitized: bool) -> ArtifactView | Notice:
        try:
            digest = await self._stores(workspace_id, session_id).put(content)
        except BlobIntegrityError:
            return Notice(name, "storage_unavailable", "存储暂时不可用，未能保存")
        row = Artifact(id=f"art_{uuid.uuid4().hex}", workspace_id=workspace_id, conversation_id=session_id,
                       run_id=run_id, filename=name, content_type=content_type_of(name), size=len(content),
                       digest=digest, source_sha256=source, sanitized=sanitized, created_at=datetime.now(UTC))
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            session.add(row)
        return artifact_view(row)
