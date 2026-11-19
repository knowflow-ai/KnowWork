"""对话里的文件按路径读与转 PDF（方案 7.7.3：网页与桌面同一套，照 DSH 官方「对话加路径」）。

回答里的文件链接、交付卡片在云端对话里都经这里读；本机会话的文件在用户电脑上，由桌面端用官方接口读，前端走同一个接口
（chat.sources 的 ConversationFiles）。

- 路径照模型写的样子收（相对路径、/workspace/...、宿主路径 /sessions/<本会话>/...），入口处（locate）解析成工作区路径一次，
  之后只用它（workpath.py）；越出工作目录的拒绝；
- 交付过的文件（回合结束时收进存储的产物）读收好的那份：沙箱回收后也在，表格是做过公式转义的那份，和交付卡片一致；
- 没交付过的从会话沙箱现读，不为读文件新建容器：沙箱空闲 30 分钟回收，回收后工作区是空的，这时如实说明；
- 转 PDF：交付过的复用产物预览（转一次、缓存）；没交付过的在沙箱里现转、不缓存（文件随时会改）；PDF 原样。
"""

import hashlib
from dataclasses import dataclass

from ..artifacts.service import ArtifactService
from ..conversations.constants import DEVICE_SOURCE
from ..conversations.models import Conversation
from ..sandbox.contract import SandboxFileError, SandboxNotStarted, SessionSandbox
from ..sandbox.docker_sandbox import CONVERTIBLE
from ..sessionfiles.names import extension
from ..workpath import InvalidPath, WorkPath, parse
from .preview import DEFAULT_LIMITS, PreviewLimits, PreviewService, PreviewUnsupported

MIB = 1024 * 1024


class FileOutside(Exception):
    """路径越出了这段对话的工作目录。"""


class FileMissing(Exception):
    """工作目录里没有这个文件。"""


class FileGone(Exception):
    """会话沙箱已回收，没交付过的文件随之没了。"""


class FilesOnDevice(Exception):
    """在用户电脑上执行的对话：文件在那台电脑上，不在服务端。"""


@dataclass(frozen=True)
class FileContent:
    name: str
    data: bytes


def _pdf_name(name: str) -> str:
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return f"{stem}.pdf"


class ConversationFiles:
    def __init__(self, artifacts: ArtifactService, sandbox: SessionSandbox, previews: PreviewService, *,
                 max_bytes: int = 20 * MIB, limits: PreviewLimits = DEFAULT_LIMITS) -> None:
        self._artifacts = artifacts
        self._sandbox = sandbox
        self._previews = previews
        self._max_bytes = max_bytes
        self._limits = limits

    @staticmethod
    def locate(conversation: Conversation, raw: str) -> WorkPath:
        """模型写的路径 → 这段对话的工作区路径（入口处只解析这一次）；在设备上执行的对话、工作目录外的路径都拒绝。"""
        if conversation.source == DEVICE_SOURCE:
            raise FilesOnDevice(conversation.id)
        try:
            return parse(raw, conversation.id)
        except InvalidPath as error:
            raise FileOutside(raw) from error

    async def read(self, conversation: Conversation, path: WorkPath) -> FileContent:
        """原件。交付过的读收好的那份，否则从沙箱现读。"""
        delivered = await self._artifacts.delivered_at(conversation, path)
        if delivered is not None:
            return FileContent(path.name, (await self._artifacts.open(conversation, delivered.id)).data)
        return FileContent(path.name, await self._live(conversation, path))

    async def digest(self, conversation: Conversation, path: WorkPath) -> str:
        """read() 读到的内容的 sha256：交付过的用收下时记的摘要，不整份读出来；没交付过的从沙箱现读再算。"""
        delivered = await self._artifacts.delivered_at(conversation, path)
        if delivered is not None and delivered.digest:
            return delivered.digest
        return hashlib.sha256((await self.read(conversation, path)).data).hexdigest()

    async def pdf(self, conversation: Conversation, path: WorkPath) -> FileContent:
        """给 PDF 阅读器的 PDF：Word、PPT 等转换后的，PDF 原样；其他类型抛 PreviewUnsupported。"""
        name = path.name
        ext = extension(name)
        if ext == "pdf":
            return await self.read(conversation, path)
        if ext not in CONVERTIBLE:
            raise PreviewUnsupported(ext)
        delivered = await self._artifacts.delivered_at(conversation, path)
        if delivered is not None:
            _, pdf = await self._previews.pdf(conversation, delivered.id)
            return FileContent(_pdf_name(name), pdf)
        data = await self._live(conversation, path)
        pdf = await self._sandbox.convert_to_pdf(conversation.workspace_id, conversation.id, ext, data,
                                                 max_bytes=self._limits.max_pdf_bytes,
                                                 timeout_seconds=self._limits.timeout_seconds)
        return FileContent(_pdf_name(name), pdf)

    async def _live(self, conversation: Conversation, path: WorkPath) -> bytes:
        try:
            return await self._sandbox.read_delivered(conversation.workspace_id, conversation.id, path,
                                                      max_bytes=self._max_bytes)
        except SandboxNotStarted as error:
            raise FileGone(path.relative) from error
        except SandboxFileError as error:
            if error.code == "not_found":
                raise FileMissing(path.relative) from error
            raise
