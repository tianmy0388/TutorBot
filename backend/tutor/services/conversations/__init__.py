"""Conversations service (2026-06-21 plan, stage 4)."""

from .schema import (
    AppendMessageRequest,
    Conversation,
    ConversationAggregate,
    ConversationDetail,
    ConversationListResponse,
    CreateConversationRequest,
    Message,
    RecoveryWarning,
    UpdateConversationRequest,
)
from .store import (
    ConversationStore,
    get_conversation_store,
    reset_conversation_store,
)

__all__ = [
    "AppendMessageRequest",
    "Conversation",
    "ConversationAggregate",
    "ConversationDetail",
    "ConversationListResponse",
    "ConversationStore",
    "CreateConversationRequest",
    "Message",
    "RecoveryWarning",
    "UpdateConversationRequest",
    "get_conversation_store",
    "reset_conversation_store",
]
