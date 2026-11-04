"""把本轮消息带的附件 id 换成交给宿主的文件说明（kernel.contract.TurnAttachment）。

原件在 /workspace/input/<input_name>；配了文档解析且已解析完的，另给解析出的 Markdown 路径。
迁移 0013 之前的旧行（PDF、Word、PPT 只放了 Markdown，parsed_name 等于 input_name）照它实际放的那份给路径。
用户传完附件马上发消息时解析多半还在跑：短暂等它出结果（有上限），等到了模型才能被告知解析稿路径，
不然模型面对目录里「来历不明」的 .md 只会自己再从 PDF 抽一遍文本。等不到或解析失败就按原件继续，
解析完成的 .md 模型在后面的轮次自然可见。
"""

import asyncio
import time
from collections.abc import Sequence

from ..conversations.models import Conversation
from ..conversations.service import AttachmentUnavailable
from ..kernel.contract import TurnAttachment
from .service import AttachmentService, AttachmentView
from .settings import AttachmentSettings
from .types import AttachmentRejected

INPUT_DIR = "/workspace/input/"
POLL_SECONDS = 0.5


def turn_attachment(item: AttachmentView) -> TurnAttachment:
    markdown = (INPUT_DIR + item.parsed_name
                if item.parsed_name and item.parsed_name != item.input_name else None)
    if item.parse_status == "ready" and markdown:  # 解析稿已就位：长文档优先读它
        return TurnAttachment(name=item.filename, path=INPUT_DIR + item.input_name, bytes=item.size,
                              sha256=item.digest, extracted_path=markdown)
    if item.parse_status == "parsing" and markdown:  # 还在解析：告诉模型预期路径，让它自行决定等或绕
        return TurnAttachment(name=item.filename, path=INPUT_DIR + item.input_name, bytes=item.size,
                              sha256=item.digest, extracted_path=markdown, parsing=True)
    return TurnAttachment(name=item.filename, path=INPUT_DIR + item.input_name, bytes=item.size,
                          sha256=item.digest)


class TurnAttachments:
    def __init__(self, service: AttachmentService, settings: AttachmentSettings) -> None:
        self._service = service
        self._wait_seconds = settings.attachment_parse_wait_seconds

    async def for_turn(self, conversation: Conversation, ids: Sequence[str]) -> tuple[TurnAttachment, ...]:
        try:
            items = await self._service.ready(conversation, ids)
            items = await self._settled(conversation, ids, items)
        except AttachmentRejected as error:
            raise AttachmentUnavailable(error.message) from error
        return tuple(turn_attachment(item) for item in items)

    async def _settled(self, conversation: Conversation, ids: Sequence[str],
                       items: tuple[AttachmentView, ...]) -> tuple[AttachmentView, ...]:
        """解析还在跑的附件等它出结果（上限 attachment_parse_wait_seconds）：ready 的才带得上解析稿路径，
        failed 的直接按原件继续（原因用户在附件上能看到）。等待上限到了还没完的，照常放行——
        turn_attachment 会把它们标成 parsing，宿主告知模型预期路径，由模型决定等或绕。"""
        deadline = time.monotonic() + self._wait_seconds
        pending = {item.id for item in items if item.parse_status == "parsing"}
        while pending and time.monotonic() < deadline:
            await asyncio.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))
            items = await self._service.ready(conversation, ids)
            pending = {item.id for item in items if item.parse_status == "parsing"}
        return items
