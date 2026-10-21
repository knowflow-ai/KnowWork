"""会话审批授权：「本会话内同类操作不再询问」。见 grants.py。"""

from .grants import ConversationRef, SessionGrants

__all__ = ["ConversationRef", "SessionGrants"]
