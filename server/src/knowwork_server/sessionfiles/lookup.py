"""接口层按当前调用者取会话：不是本人的或不存在都 404。ConversationService 满足 ConversationLookup。"""

from typing import Protocol

from fastapi import HTTPException

from ..conversations.api import Actor
from ..conversations.models import Conversation
from ..conversations.service import ConversationNotFound


class ConversationLookup(Protocol):
    async def get(self, *, conversation_id: str, workspace_id: str, user_id: str) -> Conversation: ...


async def owned_conversation(lookup: ConversationLookup, conversation_id: str, who: Actor) -> Conversation:
    try:
        return await lookup.get(conversation_id=conversation_id, workspace_id=who.workspace_id, user_id=who.user_id)
    except ConversationNotFound as error:
        raise HTTPException(status_code=404, detail="会话不存在") from error
