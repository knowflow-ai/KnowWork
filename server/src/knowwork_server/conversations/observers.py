"""事件泵观察者的组合：多个观察者依次收到同一帧，一个出错不影响其他。"""

import logging
from typing import Any

from .pump import FrameObserver

log = logging.getLogger(__name__)


def fan_out(*observers: FrameObserver) -> FrameObserver:
    chosen = tuple(observers)

    def observe(conversation_id: str, workspace_id: str, kind: str, data: Any) -> None:
        for observer in chosen:
            try:
                observer(conversation_id, workspace_id, kind, data)
            except Exception:
                log.exception("frame observer %r failed on %s", observer, conversation_id)

    return observe
