"""把本轮消息带的附件 id 换成交给宿主的文件说明（kernel.contract.TurnAttachment）。

原件在工作区的附件目录 input/<input_name>（合同里传工作区路径，给模型看哪种写法由宿主定）；配了文档解析的另给解析出的
Markdown 路径。还在解析时也给，并标明现在还没有：
网页发送前会等解析完，用户点了「直接发送」、或经接口发来的消息才会赶上解析没完，模型知道它会出现在哪，
过一会儿能自己去看，不然只能拿原件从头转一遍。
迁移 0013 之前的旧行（PDF、Word、PPT 只放了 Markdown，parsed_name 等于 input_name）照它实际放的那份给路径。

怎么读（reading.py，照 Claude 的做法）：解析好的文字、纯文本附件，放得下的整篇放进这一轮，放不下的给字数、页数和目录。
一轮的预算按附件顺序用，前面的用掉了后面的就给目录。

工作目录根上已经有同名文件的（人在编辑器里打开过这个附件，documents/editing.py 的 working_copy），另给它的路径：
宿主告诉模型要改文档就改那份，别照只读原件另起一份，把人手改的盖掉。
"""

import logging
from collections.abc import Awaitable, Callable, Sequence

from ..conversations.models import Conversation
from ..conversations.service import AttachmentUnavailable
from ..kernel.contract import OutlineEntry, TurnAttachment
from ..workpath import INPUT, ROOT
from .reading import plan
from .service import AttachmentService, AttachmentView
from .types import AttachmentRejected

log = logging.getLogger(__name__)


def _input(name: str) -> str:
    return INPUT.child(name).relative


def turn_attachment(item: AttachmentView) -> TurnAttachment:
    separate = bool(item.parsed_name) and item.parsed_name != item.input_name
    pending = separate and item.parse_status == "parsing"
    extracted = _input(item.parsed_name) if separate and (pending or item.parse_status == "ready") else None
    digesting = item.digest_status == "digesting"
    digest = _input(item.digest_name) if item.digest_name and (digesting or item.digest_status == "ready") else None
    return TurnAttachment(name=item.filename, path=_input(item.input_name), bytes=item.size,
                          sha256=item.digest, extracted_path=extracted, extraction_pending=pending,
                          digest_path=digest, digest_pending=digest is not None and digesting)


class TurnAttachments:
    def __init__(self, service: AttachmentService) -> None:
        self._service = service
        self._working_copy: Callable[[Conversation, str], Awaitable[bool]] | None = None

    def check_working_copies(self, exists: Callable[[Conversation, str], Awaitable[bool]]) -> None:
        """有扩展提供在线编辑器时由装配登记（工作副本只经它建）：exists 查工作目录根上有没有这个名字的文件
        （documents/files.py 的 exists）。没登记时发消息不多查这一次（可选服务不碰热路径）。"""
        self._working_copy = exists

    @property
    def checks_working_copies(self) -> bool:
        return self._working_copy is not None

    async def for_turn(self, conversation: Conversation, ids: Sequence[str]) -> tuple[TurnAttachment, ...]:
        """还没发出去的（输入框里暂存的）这时认领进会话、放进沙箱。"""
        try:
            items = await self._service.claim(conversation, ids)
        except AttachmentRejected as error:
            raise AttachmentUnavailable(error.message) from error
        inline_limit, budget = self._service.inline_limits
        files = []
        for item in items:
            file = turn_attachment(item)
            if await self._has_working_copy(conversation, item.input_name):
                file = file.model_copy(update={"working_copy_path": ROOT.child(item.input_name).relative})
            text = await self._service.readable_text(conversation.workspace_id, item)
            if text is None:
                files.append(file)
                continue
            reading = plan(text, inline_limit=inline_limit, budget=budget)
            if reading.text is not None:
                budget -= reading.chars
            files.append(file.model_copy(update={
                "text": reading.text, "text_chars": reading.chars, "text_pages": item.page_count or reading.pages,
                "outline": tuple(OutlineEntry(line=e.line, text=e.text) for e in reading.outline)}))
        return tuple(files)

    async def _has_working_copy(self, conversation: Conversation, name: str) -> bool:
        """根上有同名文件：多半是人在编辑器里打开附件时建的副本（也可能是模型之前放的，同样是要接着改的那份）。
        查不成（沙箱连不上等）就当没有：这句提示可有可无，不能因为它让这一轮发不出去。"""
        if self._working_copy is None:
            return False
        try:
            return await self._working_copy(conversation, name)
        except Exception:
            log.warning("没能确认附件有没有工作副本", exc_info=True)
            return False
