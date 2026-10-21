"""把本轮消息带的附件 id 换成交给宿主的文件说明（kernel.contract.TurnAttachment）。

原件在 /workspace/input/<input_name>；配了文档解析且已解析完的，另给解析出的 Markdown 路径。
迁移 0013 之前的旧行（PDF、Word、PPT 只放了 Markdown，parsed_name 等于 input_name）照它实际放的那份给路径。
"""

from collections.abc import Sequence

from ..conversations.models import Conversation
from ..conversations.service import AttachmentUnavailable
from ..kernel.contract import TurnAttachment
from .service import AttachmentService, AttachmentView
from .types import AttachmentRejected

INPUT_DIR = "/workspace/input/"


def turn_attachment(item: AttachmentView) -> TurnAttachment:
    extracted = (INPUT_DIR + item.parsed_name
                 if item.parse_status == "ready" and item.parsed_name and item.parsed_name != item.input_name
                 else None)
    return TurnAttachment(name=item.filename, path=INPUT_DIR + item.input_name, bytes=item.size,
                          sha256=item.digest, extracted_path=extracted)


class TurnAttachments:
    def __init__(self, service: AttachmentService) -> None:
        self._service = service

    async def for_turn(self, conversation: Conversation, ids: Sequence[str]) -> tuple[TurnAttachment, ...]:
        try:
            items = await self._service.ready(conversation, ids)
        except AttachmentRejected as error:
            raise AttachmentUnavailable(error.message) from error
        return tuple(turn_attachment(item) for item in items)
