"""Stream events and event types.

The Tutor system streams structured events from agents → orchestrator →
WebSocket consumers. Each event carries enough metadata for the frontend
to render trace panels, content cards, and progress indicators.

Design inspired by DeepTutor's ``StreamEvent``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StreamEventType(str, Enum):
    """All event types emitted on the stream bus.

    The values are stable wire formats — the frontend dispatches on them.
    """

    # Lifecycle
    STAGE_START = "stage_start"     # A logical stage (Agent / pipeline step) begins
    STAGE_END = "stage_end"         # A stage ends (with status)

    # Reasoning
    THINKING = "thinking"           # Internal LLM reasoning / planning
    OBSERVATION = "observation"     # Agent observation of state/data

    # Content
    CONTENT = "content"             # Streamed content chunk (delta)
    CONTENT_FINAL = "content_final" # Final aggregated content (after streaming)

    # Tool use
    TOOL_CALL = "tool_call"         # Tool invocation start
    TOOL_RESULT = "tool_result"     # Tool invocation result

    # Progress
    PROGRESS = "progress"           # Numeric progress (current/total/message)

    # References
    SOURCES = "sources"             # Citations / retrieved chunks

    # Outputs
    RESULT = "result"               # Final structured result for the turn
    RESOURCE = "resource"           # **2026-07-08:** incremental single-resource
                                    # ready event. Emitted the moment an Agent
                                    # finishes one Resource (before the final
                                    # ``RESULT``) so the frontend can render
                                    # the resource card immediately rather than
                                    # waiting for the whole pipeline to drain.

    # Errors / cancellation
    ERROR = "error"                 # Error during processing
    CANCELLED = "cancelled"         # User cancelled

    # Session
    SESSION = "session"             # Session-level info (e.g. session_id)
    DONE = "done"                   # Terminal marker for the turn
    JOB_TERMINAL = "job_terminal"   # JobRunner: normalized result contract


@dataclass
class StreamEvent:
    """A single event on the stream bus.

    Attributes
    ----------
    type : StreamEventType
        Discriminator for the frontend.
    source : str
        Originating component (Agent name, "orchestrator", "tool:rag", etc.).
    stage : str
        Current pipeline stage (e.g. "content_generation", "quality_review").
    content : str
        Main payload — for ``CONTENT`` this is a delta; for ``THINKING`` it's
        the thought text; for ``RESULT`` it's a JSON-encoded result.
    metadata : dict[str, Any]
        Free-form structured data (e.g. confidence_score, progress fields).
    session_id, turn_id : str
        Routing identifiers.
    seq : int
        Monotonic per-turn sequence number assigned by StreamBus.
    timestamp : float
        Unix seconds (assigned at construction).
    event_id : str
        UUID for dedup / correlation.
    """

    type: StreamEventType
    source: str = ""
    stage: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    session_id: str = ""
    turn_id: str = ""
    seq: int = 0
    timestamp: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-safe dict (for WebSocket framing)."""
        return {
            "type": self.type.value,
            "source": self.source,
            "stage": self.stage,
            "content": self.content,
            "metadata": self.metadata,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "seq": self.seq,
            "timestamp": self.timestamp,
            "event_id": self.event_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StreamEvent":
        """Inverse of :meth:`to_dict`."""
        return cls(
            type=StreamEventType(data["type"]),
            source=data.get("source", ""),
            stage=data.get("stage", ""),
            content=data.get("content", ""),
            metadata=data.get("metadata", {}),
            session_id=data.get("session_id", ""),
            turn_id=data.get("turn_id", ""),
            seq=data.get("seq", 0),
            timestamp=data.get("timestamp", time.time()),
            event_id=data.get("event_id", uuid.uuid4().hex),
        )


__all__ = ["StreamEvent", "StreamEventType"]
