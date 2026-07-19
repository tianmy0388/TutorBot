"""LearningEventStore — SQLite-backed persistence.

Append-only event log + simple query helpers. Each event is stored as
JSON to keep the schema flexible (no DB migrations needed when we add
event types).
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import JSON, BigInteger, Column, DateTime, Index, Integer, String, func, select, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import Integer as SqlInteger

from tutor.services.config.settings import get_settings
from tutor.services.exercise_responses.schema import ExerciseQuestionType
from tutor.services.learning_events.schema import (
    EventType,
    LearningEvent,
)


class EventConflictError(ValueError):
    """An event_id was reused with a different durable payload."""

    code = "LEARNING_EVENT_CONFLICT"

    def __init__(self, event_id: str) -> None:
        super().__init__("learning event id conflicts with existing evidence")
        self.event_id = event_id


@dataclass(frozen=True)
class AppendResult:
    event: LearningEvent
    inserted: bool


class _Base(DeclarativeBase):
    pass


class EventRow(_Base):
    """One event row (one LearningEvent JSON blob)."""

    __tablename__ = "learning_events"

    id = Column(
        BigInteger().with_variant(SqlInteger, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    event_id = Column(String(64), nullable=False, unique=True)
    user_id = Column(String(128), nullable=False, index=True)
    session_id = Column(String(64), nullable=False, default="")
    event_type = Column(String(64), nullable=False, index=True)
    target_id = Column(String(128), nullable=False, default="")
    concept_id = Column(String(128), nullable=False, default="", index=True)
    duration_seconds = Column(Integer, nullable=False, default=0)
    score = Column(Integer, nullable=True)
    correct = Column(Integer, nullable=True)  # 0 / 1 / null
    event_data = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)

    __table_args__ = (
        Index("ix_events_user_time", "user_id", "created_at"),
        Index("ix_events_user_type_time", "user_id", "event_type", "created_at"),
    )


class LearningEventStore:
    """Async SQLite store for LearningEvents."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = get_settings().data_dir / "learning_events.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None
        self._write_lock: asyncio.Lock | None = None
        self._lock = threading.Lock()

    async def init(self) -> None:
        engine = self._ensure_engine()
        async with engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
            columns = {
                str(row[1])
                for row in (await conn.exec_driver_sql("PRAGMA table_info(learning_events)")).fetchall()
            }
            if "session_id" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE learning_events ADD COLUMN session_id VARCHAR(64) NOT NULL DEFAULT ''"
                )
        logger.info(f"LearningEventStore ready at {self.db_path}")

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None
            self._write_lock = None

    def _ensure_engine(self) -> AsyncEngine:
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    url = f"sqlite+aiosqlite:///{self.db_path}"
                    self._engine = create_async_engine(
                        url,
                        echo=False,
                        future=True,
                        connect_args={"check_same_thread": False},
                    )
                    self._sessionmaker = async_sessionmaker(
                        self._engine, expire_on_commit=False
                    )
                    self._write_lock = asyncio.Lock()
        return self._engine

    async def append(self, event: LearningEvent) -> AppendResult:
        """Append once by event_id; equal retries are idempotent."""
        if not event.event_id:
            event.event_id = uuid.uuid4().hex
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            existing = (
                await session.execute(
                    select(EventRow).where(EventRow.event_id == event.event_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                persisted = self._row_to_event(existing)
                if self._fingerprint(persisted) != self._fingerprint(event):
                    raise EventConflictError(event.event_id)
                return AppendResult(event=persisted, inserted=False)
            row = EventRow(
                event_id=event.event_id,
                user_id=event.user_id,
                session_id=event.session_id,
                event_type=event.event_type.value,
                target_id=event.target_id,
                concept_id=event.concept_id,
                duration_seconds=event.duration_seconds,
                score=(
                    int(round(event.score * 1000))
                    if event.score is not None
                    else None
                ),
                correct=(
                    1 if event.correct is True
                    else (0 if event.correct is False else None)
                ),
                event_data=event.to_dict(),
                created_at=event.created_at,
            )
            session.add(row)
            await session.flush()
            event.sequence = int(row.id)
        return AppendResult(event=event, inserted=True)

    async def record(self, event: LearningEvent) -> LearningEvent:
        """Compatibility wrapper around idempotent append."""
        return (await self.append(event)).event

    async def get_for_user(
        self,
        event_id: str,
        user_id: str,
    ) -> LearningEvent | None:
        """Read one event by stable ID without crossing the owner boundary."""
        self._ensure_engine()
        async with self._with_session() as session:
            row = (
                await session.execute(
                    select(EventRow).where(
                        EventRow.event_id == event_id,
                        EventRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            return self._row_to_event(row) if row is not None else None

    async def record_many(self, events: Iterable[LearningEvent]) -> int:
        """Append multiple events in one transaction. Returns count."""
        self._ensure_engine()
        assert self._write_lock is not None
        events_list = list(events)
        if not events_list:
            return 0
        async with self._write_lock, self._with_session() as session:
            for ev in events_list:
                if not ev.event_id:
                    ev.event_id = uuid.uuid4().hex
                session.add(
                    EventRow(
                        event_id=ev.event_id,
                        user_id=ev.user_id,
                        session_id=ev.session_id,
                        event_type=ev.event_type.value,
                        target_id=ev.target_id,
                        concept_id=ev.concept_id,
                        duration_seconds=ev.duration_seconds,
                        score=(
                            int(round(ev.score * 1000))
                            if ev.score is not None
                            else None
                        ),
                        correct=(
                            1 if ev.correct is True
                            else (0 if ev.correct is False else None)
                        ),
                        event_data=ev.to_dict(),
                        created_at=ev.created_at,
                    )
                )
        return len(events_list)

    async def count_scored_since(self, user_id: str, watermark: int) -> int:
        """Count scored evidence after a durable monotonic watermark."""
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(func.count(EventRow.id)).where(
                EventRow.user_id == user_id,
                EventRow.id > watermark,
                EventRow.event_type == EventType.EXERCISE_SCORED.value,
                EventRow.score.is_not(None),
            )
            return int((await session.execute(stmt)).scalar_one())

    async def list_since(
        self,
        user_id: str,
        watermark: int,
        *,
        through_sequence: int | None = None,
    ) -> list[LearningEvent]:
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(EventRow).where(
                EventRow.user_id == user_id,
                EventRow.id > watermark,
            )
            if through_sequence is not None:
                stmt = stmt.where(EventRow.id <= through_sequence)
            rows = (await session.execute(stmt.order_by(EventRow.id))).scalars().all()
            return [self._row_to_event(row) for row in rows]

    async def profile_trigger_sequence_since(
        self,
        user_id: str,
        watermark: int,
        *,
        through_sequence: int | None = None,
        scored_threshold: int = 5,
    ) -> int | None:
        """Return the earliest durable sequence that satisfies a profile trigger."""
        scored = 0
        for event in await self.list_since(
            user_id,
            watermark,
            through_sequence=through_sequence,
        ):
            if event.event_type == EventType.ASSESSMENT_COMPLETED:
                return event.sequence
            if (
                event.event_type in {
                    EventType.EXERCISE_ATTEMPTED,
                    EventType.EXERCISE_SCORED,
                }
                and event.score is not None
            ):
                scored += 1
                if scored >= scored_threshold:
                    return event.sequence
        return None

    async def latest_sequence(self, user_id: str) -> int:
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(func.max(EventRow.id)).where(EventRow.user_id == user_id)
            return int((await session.execute(stmt)).scalar_one() or 0)

    async def has_assessment_since(self, user_id: str, watermark: int) -> bool:
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(func.count(EventRow.id)).where(
                EventRow.user_id == user_id,
                EventRow.id > watermark,
                EventRow.event_type == EventType.ASSESSMENT_COMPLETED.value,
            )
            return bool((await session.execute(stmt)).scalar_one())

    async def query(
        self,
        user_id: str,
        *,
        since: datetime | None = None,
        until: datetime | None = None,
        event_types: list[EventType] | None = None,
        concept_id: str | None = None,
        limit: int = 100,
    ) -> list[LearningEvent]:
        """Query events for a user."""
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(EventRow).where(EventRow.user_id == user_id)
            if since is not None:
                stmt = stmt.where(EventRow.created_at >= since)
            if until is not None:
                stmt = stmt.where(EventRow.created_at <= until)
            if event_types:
                stmt = stmt.where(
                    EventRow.event_type.in_([e.value for e in event_types])
                )
            if concept_id:
                stmt = stmt.where(EventRow.concept_id == concept_id)
            stmt = stmt.order_by(EventRow.created_at.desc()).limit(limit)
            rows = (await session.execute(stmt)).scalars().all()
            return [
                self._row_to_event(r) for r in rows
            ]

    async def recent_exercise_evidence(
        self,
        user_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return a bounded, answer-safe projection of recent scored evidence."""
        bounded_limit = max(0, min(int(limit), 100))
        if bounded_limit == 0:
            return []
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = (
                select(EventRow)
                .where(
                    EventRow.user_id == user_id,
                    EventRow.event_type == EventType.EXERCISE_SCORED.value,
                    EventRow.score.is_not(None),
                )
                .order_by(EventRow.created_at.desc(), EventRow.id.desc())
                .limit(bounded_limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            evidence = []
            for row in rows:
                event = self._row_to_event(row)
                raw_question_type = event.metadata.get("question_type")
                try:
                    question_type = (
                        ExerciseQuestionType(raw_question_type).value
                        if isinstance(raw_question_type, str)
                        else ""
                    )
                except ValueError:
                    question_type = ""
                evidence.append(
                    {
                        "event_id": event.event_id,
                        "concept_id": event.concept_id,
                        "score": event.score,
                        "question_type": question_type,
                        "created_at": event.created_at.isoformat(),
                    }
                )
            return evidence

    async def stats(
        self,
        user_id: str,
        *,
        window_hours: int = 168,
    ) -> dict[str, Any]:
        """Aggregate stats for a user over a time window."""
        since = datetime.now(UTC) - timedelta(hours=window_hours)
        events = await self.query(user_id, since=since, limit=10000)
        if not events:
            return {
                "event_count": 0,
                "by_type": {},
                "total_duration_seconds": 0,
                "concepts_touched": [],
                "exercise_score_avg": None,
                "completion_rate": 0.0,
            }

        by_type: Counter[str] = Counter(e.event_type.value for e in events)
        total_duration = sum(e.duration_seconds for e in events)
        concepts = {e.concept_id for e in events if e.concept_id}

        exercise_scores = [
            e.score for e in events
            if e.event_type in (
                EventType.EXERCISE_ATTEMPTED,
                EventType.EXERCISE_COMPLETED,
                EventType.EXERCISE_SCORED,
            )
            and e.score is not None
        ]
        avg_score = (
            sum(exercise_scores) / len(exercise_scores)
            if exercise_scores
            else None
        )

        completed = by_type.get(EventType.RESOURCE_COMPLETED.value, 0)
        viewed = by_type.get(EventType.RESOURCE_VIEWED.value, 0)
        completion_rate = completed / viewed if viewed > 0 else 0.0

        return {
            "event_count": len(events),
            "by_type": dict(by_type),
            "total_duration_seconds": total_duration,
            "concepts_touched": sorted(concepts),
            "exercise_score_avg": avg_score,
            "completion_rate": completion_rate,
            "window_hours": window_hours,
        }

    async def list_users(self) -> list[str]:
        """Return all distinct user IDs (for housekeeping)."""
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(EventRow.user_id).distinct()
            rows = (await session.execute(stmt)).scalars().all()
            return sorted(set(rows))

    def _with_session(self):
        """Context manager: yield a session inside a transaction."""
        if self._sessionmaker is None:
            self._ensure_engine()
        assert self._sessionmaker is not None

        store = self

        class _Ctx:
            async def __aenter__(self):
                self._s = store._sessionmaker()  # type: ignore[union-attr]
                return self._s

            async def __aexit__(self, exc_type, exc, tb):
                try:
                    if exc_type is None:
                        await self._s.commit()
                    else:
                        await self._s.rollback()
                finally:
                    await self._s.close()

        return _Ctx()

    @staticmethod
    def _row_to_event(row: EventRow) -> LearningEvent:
        data = dict(row.event_data or {})  # copy to mutate
        # Restore score scaling (we store int*1000)
        if row.score is not None and "score" not in data:
            data["score"] = row.score / 1000.0
        # Restore correct
        if row.correct is not None and "correct" not in data:
            data["correct"] = row.correct == 1
        # created_at may be string, datetime, or None — normalize to ISO string
        # (LearningEvent.from_dict expects string per its logic, or a datetime
        # which it passes through to the dataclass — but the dataclass expects
        # a datetime; if we already converted it, skip)
        created_at = data.get("created_at")
        if created_at is None:
            data["created_at"] = datetime.now(UTC).isoformat()
        elif isinstance(created_at, datetime):
            # Already a datetime — leave it (LearningEvent dataclass accepts datetime)
            pass
        elif isinstance(created_at, str):
            # Try parsing; if it fails, just use now
            try:
                datetime.fromisoformat(created_at)
            except (ValueError, TypeError):
                data["created_at"] = datetime.now(UTC).isoformat()
        else:
            data["created_at"] = datetime.now(UTC).isoformat()
        # Trust the indexed ownership column over a stale denormalized JSON
        # value left by an earlier local single-user migration.
        data["user_id"] = row.user_id
        data["sequence"] = int(row.id)
        data["session_id"] = row.session_id or data.get("session_id", "")
        return LearningEvent.from_dict(data)

    @staticmethod
    def _fingerprint(event: LearningEvent) -> str:
        data = event.to_dict()
        data.pop("created_at", None)
        data.pop("sequence", None)
        return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


_store: LearningEventStore | None = None
_store_lock = threading.Lock()


def get_learning_event_store() -> LearningEventStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = LearningEventStore()
                logger.info("LearningEventStore singleton created")
    return _store


def reset_learning_event_store() -> None:
    global _store
    _store = None


__all__ = [
    "AppendResult",
    "EventConflictError",
    "LearningEventStore",
    "get_learning_event_store",
    "reset_learning_event_store",
]
