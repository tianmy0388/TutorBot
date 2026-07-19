"""Conversation persistence (Task 4 of the 2026-06-21 plan).

A ``Conversation`` is a logical chat session — a thread of user +
assistant messages tied to a ``session_id``. They survive a backend
restart (SQLite) so the history sidebar in the UI can list prior
sessions and let the user resume them.

Scope of this first cut:

- One conversation per ``session_id`` (idempotent create).
- Append-only message log; no editing or branching.
- Title auto-generated from the first user message.
- Cursor pagination on list (newest first).
- All endpoints are scoped to ``user_id`` for isolation.

Anti-hallucination & safety: messages are stored verbatim — no
model-derived rewriting, no embedding here. Embedding and RAG are
the KnowledgeBase service's job. This service is a thin
persistence layer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tutor.services.resource_package.schema import ResourcePackage

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


MessageRole = Literal["user", "assistant", "system"]
RecoveryWarningCode = Literal[
    "migrated_ownership",
    "interrupted_job_repaired",
    "missing_artifact",
    "recovery_association_missing",
]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Message(BaseModel):
    """One chat message inside a conversation."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    role: MessageRole
    content: str
    job_id: str | None = None
    # The capability that produced the assistant message, if any.
    capability: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class Conversation(BaseModel):
    """A thread of messages tied to a ``session_id``."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    user_id: str
    title: str = ""
    message_count: int = 0
    last_message_preview: str = ""
    web_search_enabled: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationDetail(Conversation):
    """Conversation plus its full message list."""

    messages: list[Message] = Field(default_factory=list)


class RecoveryWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: RecoveryWarningCode
    message: str
    job_id: str | None = None
    package_id: str | None = None
    resource_id: str | None = None
    artifact_key: str | None = None


class ConversationAggregate(BaseModel):
    """One refresh-safe payload for restoring an entire conversation view."""

    conversation: ConversationDetail
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    packages: list[ResourcePackage] = Field(default_factory=list)
    profile_summary: dict[str, Any] = Field(default_factory=dict)
    path_summary: dict[str, Any] = Field(default_factory=dict)
    recovery_warnings: list[RecoveryWarning] = Field(default_factory=list)


class ConversationListResponse(BaseModel):
    items: list[Conversation]
    total: int
    limit: int
    offset: int
    has_more: bool


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None
    user_id: str
    title: str | None = None
    # Applied only when the row is first created. Repeating POST for an
    # existing session remains idempotent and never acts as a settings PATCH.
    web_search_enabled: bool = False


class AppendMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: MessageRole
    content: str
    job_id: str | None = None
    capability: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None


class UpdateConversationSettingsRequest(BaseModel):
    """Narrow per-conversation settings mutation contract."""

    model_config = ConfigDict(extra="forbid")

    web_search_enabled: bool


__all__ = [
    "AppendMessageRequest",
    "Conversation",
    "ConversationDetail",
    "ConversationAggregate",
    "ConversationListResponse",
    "CreateConversationRequest",
    "Message",
    "RecoveryWarning",
    "UpdateConversationRequest",
    "UpdateConversationSettingsRequest",
]
