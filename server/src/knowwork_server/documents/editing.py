"""扩展编辑对话里的文件（例如网页里的 Office 编辑器）：核对对话归本人、读最新的一份、存盘成新的一版并写回会话沙箱。

和读对话文件（files.py）同一口径：交付过的读收好的那份，没交付过的从会话沙箱现读。存盘走产物收集器（表格转义、单文件与累计上限、
按摘要存储），同一路径多一行就是新的一版（delivered_at 取最新的），下一轮模型、交付卡片、预览看到的都是改过的；会话沙箱还在就
顺手写回工作目录，模型在沙箱里接着改的是人改过的版本（和最新一版一样时不另存、也不写回）。
- 文件用工作区路径（workpath.py）认，同一份文件在沙箱里、编辑器里各在哪由各自的挂载点换算；
- 附件目录里的不让改：约定附件由系统放入、只读（workpath.INPUT）。要编辑附件时改它在工作目录根上的同名副本
  （working_copy）：模型这一轮读到的附件原文、「原样只读副本」的说法都还成立，人和模型改的是同一份副本；
  输入框里还没发出去的附件也能直接这样打开（draft_copy），先认领进对话再建副本，不用先发一句话；
- 在设备上执行的对话（桌面端的本机会话）文件在那台电脑上，桌面端自己编辑，这里不管；
- 能编辑哪些格式由扩展决定。
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ..artifacts.collector import ArtifactCollector, Notice
from ..conversations.models import Conversation
from ..conversations.service import ConversationNotFound
from ..sandbox.contract import SandboxError, SessionSandbox
from ..sessionfiles.lookup import ConversationLookup
from ..workpath import INPUT, ROOT, WorkPath
from .errors import describe
from .files import ConversationFiles

log = logging.getLogger(__name__)

# 把这个人还没发出去的一个附件认领进对话、放进附件目录（附件服务的 claim_draft），返回它在附件目录里的名字；
# 不是他的、不存在、还没传完、第三个参数（看文件名）不认、放不进沙箱都返回 None
DraftClaim = Callable[[Conversation, str, Callable[[str], bool]], Awaitable[str | None]]


class EditRefused(Exception):
    """不能打开或存：消息直接给人看，status 是建议的 HTTP 状态码。"""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class EditableFile:
    conversation: Conversation
    path: WorkPath  # 工作区路径，例如 output/报告.docx

    @property
    def relative(self) -> str:
        return self.path.relative

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(frozen=True)
class SavedEdit:
    changed: bool  # False：和这个路径最新的一版一样，没有另存
    artifact_id: str | None  # 存成的新一版
    synced: bool  # 写回了还在的会话沙箱


def _refusal(error: Exception) -> EditRefused | None:
    described = describe(error)
    return EditRefused(described[1], described[0]) if described is not None else None


class ConversationFileEditing:
    def __init__(self, conversations: ConversationLookup, files: ConversationFiles, collector: ArtifactCollector,
                 sandbox: SessionSandbox, *, settle: Callable[[str], Awaitable[None]] | None = None,
                 claim_draft: DraftClaim | None = None) -> None:
        self._conversations = conversations
        self._files = files
        self._collector = collector
        self._sandbox = sandbox
        # 一轮结束时产物在后台收（artifacts/observer.py）：读之前等这段对话正在收的落库，刚结束的这一轮交付的才读得到
        self._settle = settle
        self._claim_draft = claim_draft

    async def locate(self, *, workspace_id: str, conversation_id: str, user_id: str, path: str) -> EditableFile:
        """这个人能编辑的这份文件；对话不是本人的、在设备上执行、路径在工作目录外或在附件目录里都拒绝。"""
        conversation, work_path = await self._resolve(workspace_id, conversation_id, user_id, path)
        if work_path.is_root:
            raise EditRefused("只能编辑这段对话工作目录里的文件", 400)
        if work_path.within(INPUT):
            raise EditRefused("附件是只读的，不能直接改；可以让智能体另存一份再编辑", 409)
        return EditableFile(conversation, work_path)

    async def working_copy(self, *, workspace_id: str, conversation_id: str, user_id: str,
                           path: str) -> EditableFile:
        """附件编辑用的副本：工作目录根上和附件同名的那份。根上已经有同名文件（上次点开改过的、模型自己放的）就用它，
        不拿原件盖掉；没有就从附件复制一份，存成这个路径的一版（沙箱回收了也在）并写回还在的沙箱。拒绝的规则同 locate，
        另外只收附件目录里的文件。"""
        conversation, source = await self._resolve(workspace_id, conversation_id, user_id, path)
        if source.relative != f"{INPUT.relative}/{source.name}":
            raise EditRefused("只有附件需要建副本", 400)
        return await self._copy(conversation, source)

    async def draft_copy(self, *, workspace_id: str, conversation_id: str, user_id: str, attachment_id: str,
                         editable: Callable[[str], bool]) -> EditableFile:
        """输入框里还没发出去的附件直接进编辑器，不用先发一句话：先认领进这段对话（放进附件目录，和发消息时认领的是同一行，
        之后随消息发出去照用、不再放一遍，名字也就和消息上附件卡片的一样），再照 working_copy 建副本。
        只收本人暂存的、editable 认的格式；在设备上执行的对话在认领之前就拒绝。"""
        conversation, _ = await self._resolve(workspace_id, conversation_id, user_id, INPUT.relative)
        if self._claim_draft is None:
            raise EditRefused("这里不能直接编辑还没发出去的附件", 404)
        name = await self._claim_draft(conversation, attachment_id, editable)
        if name is None:
            raise EditRefused("找不到这个附件（可能还没传完），或者它不能在线编辑", 404)
        return await self._copy(conversation, INPUT.child(name))

    async def _copy(self, conversation: Conversation, source: WorkPath) -> EditableFile:
        copy = EditableFile(conversation, ROOT.child(source.name))
        if self._settle is not None:
            await self._settle(conversation.id)
        if await self._exists(copy):
            return copy
        original = await self._guarded(self._files.read(conversation, source))
        await self.save(copy, original.data)
        return copy

    async def _resolve(self, workspace_id: str, conversation_id: str, user_id: str,
                       path: str) -> tuple[Conversation, WorkPath]:
        try:
            conversation = await self._conversations.get(conversation_id=conversation_id, workspace_id=workspace_id,
                                                         user_id=user_id)
        except ConversationNotFound as error:
            raise EditRefused("会话不存在", 404) from error
        try:
            return conversation, self._files.locate(conversation, path)
        except Exception as error:
            refusal = _refusal(error)
            if refusal is None:
                raise
            raise refusal from error

    async def _exists(self, file: EditableFile) -> bool:
        return await self._guarded(self._files.exists(file.conversation, file.path))

    @staticmethod
    async def _guarded[T](pending: Awaitable[T]) -> T:
        try:
            return await pending
        except Exception as error:
            refusal = _refusal(error)
            if refusal is None:
                raise
            raise refusal from error

    async def read(self, file: EditableFile) -> bytes:
        """这个路径最新的一版（交付过的读收好的，没交付过的从沙箱现读）。"""
        return (await self._latest(file, self._files.read)).data

    async def latest_digest(self, file: EditableFile) -> str:
        """最新一版的 sha256（同 read() 读到的内容）：扩展用它比编辑器里那份变没变，交付过的不用整份读出来。"""
        return await self._latest(file, self._files.digest)

    async def _latest[T](self, file: EditableFile, get: Callable[[Conversation, WorkPath], Awaitable[T]]) -> T:
        if self._settle is not None:
            await self._settle(file.conversation.id)
        return await self._guarded(get(file.conversation, file.path))

    async def save(self, file: EditableFile, data: bytes) -> SavedEdit:
        conversation = file.conversation
        stored = await self._collector.store_edit(conversation.workspace_id, conversation.id, file.path, data)
        if isinstance(stored, Notice):
            status = 413 if stored.code in ("file_too_large", "session_quota") else 422
            raise EditRefused(f"没能存成新的一版：{stored.message}", status)
        if stored is None:
            # 和最新一版一样：不另存，也不写回沙箱（不白搬一份，也不把模型在沙箱里接着改、还没交付的那份盖回去）
            return SavedEdit(changed=False, artifact_id=None, synced=False)
        try:
            synced = await self._sandbox.write_back(conversation.workspace_id, conversation.id, file.path, data)
        except SandboxError:
            # 新的一版已经存好，写回失败只影响模型在沙箱里接着改的那份；读文件仍先读存好的
            log.warning("编辑后的文件没能写回会话沙箱", exc_info=True)
            synced = False
        return SavedEdit(changed=True, artifact_id=stored.id, synced=synced)
