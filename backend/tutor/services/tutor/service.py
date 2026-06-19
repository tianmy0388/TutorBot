"""TutorService — per-user tutoring history + RAG context retrieval.

Lightweight service backed by an in-memory store (per-process). For
production we'd persist to SQLite or Redis; for MVP the in-memory
list is enough to demonstrate the flow.

Public API
---------

- :meth:`retrieve_context` — keyword-overlap KB search
- :meth:`record_interaction` — append a Q&A turn
- :meth:`get_history` — fetch recent turns for a user
"""

from __future__ import annotations

import re
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger

from tutor.agents.tutor.question_understanding import QuestionUnderstanding
from tutor.agents.tutor.tutoring import TutoringAnswer
from tutor.services.config.settings import get_settings


# ---------------------------------------------------------------------------
# Stopwords (reuse fact_check set)
# ---------------------------------------------------------------------------


_STOPWORDS = set(
    "the a an and or but if is are was were be been have has do does "
    "this that these those i you he she it we they "
    "的 了 在 是 和 与 及 或 也 但 而 等 这 那 我 你 他 她 它 我们 "
    "你们 他们 一个 一些 这个 那个 这种 那种 是的 不是 可以 可能 应该".split()
)


def _tokenize(text: str) -> list[str]:
    parts = re.findall(r"[A-Za-z]+|[一-鿿]+", text or "")
    out: list[str] = []
    for p in parts:
        p_low = p.lower()
        if len(p_low) < 2:
            continue
        if p_low in _STOPWORDS or p in _STOPWORDS:
            continue
        out.append(p_low)
    return out


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TutorTurn:
    """One student Q&A interaction."""

    user_id: str
    question: str
    understanding: QuestionUnderstanding
    answer: TutoringAnswer
    enrichments: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "question": self.question,
            "understanding": self.understanding.to_dict(),
            "answer": self.answer.to_dict(),
            "enrichments": list(self.enrichments),
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class TutorSession:
    """Aggregate session info for one user."""

    user_id: str
    turns: list[TutorTurn] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def last_n(self, n: int = 5) -> list[TutorTurn]:
        return self.turns[-n:]

    def common_concepts(self, top_k: int = 5) -> list[tuple[str, int]]:
        c: Counter[str] = Counter()
        for t in self.turns:
            for concept in t.understanding.concepts:
                c[concept] += 1
        return c.most_common(top_k)


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TutorService:
    """In-memory tutoring history + KB context retrieval."""

    def __init__(
        self,
        *,
        kb_dir: Path | None = None,
        max_history_per_user: int = 50,
        retrieval_top_k: int = 4,
        retrieval_window: int = 500,
    ) -> None:
        self.kb_dir = Path(kb_dir) if kb_dir else get_settings().kb_dir
        self.max_history_per_user = max(1, max_history_per_user)
        self.retrieval_top_k = max(1, retrieval_top_k)
        self.retrieval_window = max(100, retrieval_window)
        self._sessions: dict[str, TutorSession] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def get_session(self, user_id: str) -> TutorSession:
        """Thread-safe session lookup; creates a new session if needed."""
        # Fast path: already exists
        sess = self._sessions.get(user_id)
        if sess is not None:
            return sess
        # Slow path: create under lock
        with self._lock:
            sess = self._sessions.get(user_id)
            if sess is None:
                sess = TutorSession(user_id=user_id)
                self._sessions[user_id] = sess
            return sess

    def _get_session_locked(self, user_id: str) -> TutorSession:
        """Caller MUST hold ``_lock``."""
        sess = self._sessions.get(user_id)
        if sess is None:
            sess = TutorSession(user_id=user_id)
            self._sessions[user_id] = sess
        return sess

    def get_history(self, user_id: str, limit: int = 10) -> list[TutorTurn]:
        sess = self.get_session(user_id)
        return list(sess.last_n(limit))

    def record_interaction(
        self,
        *,
        user_id: str,
        question: str,
        understanding: QuestionUnderstanding,
        answer: TutoringAnswer,
        enrichments: Iterable[dict[str, Any]] = (),
    ) -> TutorTurn:
        turn = TutorTurn(
            user_id=user_id,
            question=question,
            understanding=understanding,
            answer=answer,
            enrichments=list(enrichments),
        )
        with self._lock:
            sess = self._get_session_locked(user_id)
            sess.turns.append(turn)
            # Trim history
            if len(sess.turns) > self.max_history_per_user:
                sess.turns = sess.turns[-self.max_history_per_user :]
        logger.debug(
            f"TutorService: recorded turn for {user_id} "
            f"(session now has {len(sess.turns)} turns)"
        )
        return turn

    def reset(self, user_id: str | None = None) -> None:
        with self._lock:
            if user_id is None:
                self._sessions.clear()
            else:
                self._sessions.pop(user_id, None)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    async def retrieve_context(
        self,
        *,
        question: str,
        concepts: list[str] | None = None,
        source_documents: list[str] | None = None,
    ) -> str:
        """Build a RAG-style context blob from KB snippets.

        Combines keyword + concept overlap. Top-K snippets are stitched
        into a Markdown-ish blob the TutoringAgent can quote.
        """
        snippets = self._retrieve_snippets(
            question=question,
            concepts=concepts or [],
            source_documents=source_documents,
        )
        if not snippets:
            return ""
        # Stitch — tuples are (score, path, snippet)
        parts: list[str] = []
        for score, path, snippet in snippets:
            parts.append(f"### [{path}]\n{snippet}\n")
        return "\n".join(parts)

    def _retrieve_snippets(
        self,
        *,
        question: str,
        concepts: list[str],
        source_documents: list[str] | None,
    ) -> list[tuple[str, str, float]]:
        """Return ``[(path, snippet, score)]`` ranked by relevance."""
        candidates: list[Path] = []
        if source_documents:
            candidates = [Path(p) for p in source_documents if Path(p).exists()]
        if not candidates:
            candidates = self._discover_kb_files()

        if not candidates:
            return []

        # Token pool: question + concepts (concepts weighted higher)
        q_tokens = set(_tokenize(question))
        c_tokens = set(_tokenize(" ".join(concepts or [])))
        if not q_tokens and not c_tokens:
            return []

        scored: list[tuple[float, str, str]] = []
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            snippet, score = self._best_snippet(
                text, q_tokens, c_tokens, window=self.retrieval_window
            )
            if score > 0:
                scored.append((score, str(path), snippet))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[: self.retrieval_top_k]

    def _discover_kb_files(self) -> list[Path]:
        if not self.kb_dir.exists():
            return []
        return [p for p in self.kb_dir.rglob("*.md") if p.is_file()]

    @staticmethod
    def _best_snippet(
        text: str,
        q_tokens: set[str],
        c_tokens: set[str],
        window: int,
    ) -> tuple[str, float]:
        """Find the highest-scoring snippet using token overlap."""
        text_lower = text.lower()
        best_score = 0.0
        best_start = 0
        step = max(50, window // 4)
        for start in range(0, max(1, len(text) - window), step):
            end = min(len(text), start + window)
            chunk_lower = text_lower[start:end]
            chunk_tokens = set(_tokenize(chunk_lower))
            # Score: question overlap (1x) + concept overlap (2x)
            score = len(chunk_tokens & q_tokens) + 2 * len(chunk_tokens & c_tokens)
            if score > best_score:
                best_score = score
                best_start = start
        if best_score == 0:
            return "", 0.0
        snippet = text[best_start : best_start + window].strip()
        return snippet, best_score


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


_service: TutorService | None = None
_service_lock = threading.Lock()


def get_tutor_service() -> TutorService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = TutorService()
                logger.info(
                    f"TutorService ready (kb_dir={_service.kb_dir})"
                )
    return _service


def reset_tutor_service() -> None:
    global _service
    _service = None


__all__ = [
    "TutorService",
    "TutorSession",
    "TutorTurn",
    "get_tutor_service",
    "reset_tutor_service",
]
