"""UnifiedContext — request-scoped context object passed through the pipeline.

This is the "envelope" every Capability / Agent receives. It carries:

- The original user message and any attachments
- Conversation history (for context-aware agents)
- Routing info (session_id, turn_id, user_id)
- Capability / tool / model selection
- A handle to the StreamBus for emitting events

The pattern mirrors DeepTutor's UnifiedContext but is adapted for the
Tutor learning use case (learner profile, knowledge base selection, etc.).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from tutor.core.stream_bus import StreamBus


@dataclass
class UnifiedContext:
    """Request-scoped context for a single chat turn.

    Attributes
    ----------
    session_id, turn_id, job_id : str
        Routing identifiers (auto-generated if not provided).
    user_id : str
        The student user (defaults to ``"anonymous"`` in MVP).
    user_message : str
        Raw natural-language message from the user.
    history : list[dict]
        Prior turns — each item is a dict with ``role``/``content`` keys.
    attachments : list[dict]
        Files / images attached to the message.
    capability : str | None
        Capability name chosen by the router (e.g. ``"resource_generation"``).
    tool_choice : list[str]
        Restrict which tools the LLM may invoke (empty = no restriction).
    model_override : dict | None
        Per-request override of model settings (provider, model name, ...).
    language : str
        UI / content language (``"zh"`` or ``"en"``).
    metadata : dict
        Free-form per-turn metadata (e.g. learner_profile snapshot).
    stream : StreamBus | None
        The bus to emit events on. Set by the orchestrator before dispatch.
    """

    session_id: str = ""
    turn_id: str = ""
    job_id: str = ""
    user_id: str = "anonymous"
    user_message: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    capability: str | None = None
    tool_choice: list[str] = field(default_factory=list)
    model_override: dict[str, Any] | None = None
    language: str = "zh"
    metadata: dict[str, Any] = field(default_factory=dict)
    stream: StreamBus | None = None

    def __post_init__(self) -> None:
        if not self.session_id:
            self.session_id = uuid.uuid4().hex
        if not self.turn_id:
            self.turn_id = uuid.uuid4().hex

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    @property
    def stream_bus(self) -> StreamBus:
        """Get or lazily create a StreamBus for this context."""
        if self.stream is None:
            self.stream = StreamBus(session_id=self.session_id, turn_id=self.turn_id)
        return self.stream

    def with_capability(self, name: str) -> UnifiedContext:
        """Return a shallow copy with ``capability`` set."""
        from dataclasses import replace

        return replace(self, capability=name)

    def to_dict(self) -> dict[str, Any]:
        """Serialise for logging / persistence (stream bus excluded)."""
        return {
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "job_id": self.job_id,
            "user_id": self.user_id,
            "user_message": self.user_message,
            "history_count": len(self.history),
            "attachments_count": len(self.attachments),
            "capability": self.capability,
            "tool_choice": self.tool_choice,
            "model_override": self.model_override,
            "language": self.language,
            "metadata_keys": list(self.metadata.keys()),
        }


__all__ = ["UnifiedContext"]
