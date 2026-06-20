"""LearningEventStore — SQLite-backed persistence.

Append-only event log + simple query helpers. Each event is stored as
JSON to keep the schema flexible (no DB migrations needed when we add
event types).
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import JSON, BigInteger, Column, DateTime, Index, Integer, String, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import Integer as SqlInteger

from tutor.services.config.settings import get_settings
from tutor.services.learning_events.schema import (
    EventType,
    LearningEvent,
)


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

    async def record(self, event: LearningEvent) -> LearningEvent:
        """Append one event."""
        if not event.event_id:
            event.event_id = uuid.uuid4().hex
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock:
            async with self._with_session() as session:
                row = EventRow(
                    event_id=event.event_id,
                    user_id=event.user_id,
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
        return event

    async def record_many(self, events: Iterable[LearningEvent]) -> int:
        """Append multiple events in one transaction. Returns count."""
        self._ensure_engine()
        assert self._write_lock is not None
        events_list = list(events)
        if not events_list:
            return 0
        async with self._write_lock:
            async with self._with_session() as session:
                for ev in events_list:
                    if not ev.event_id:
                        ev.event_id = uuid.uuid4().hex
                    session.add(
                        EventRow(
                            event_id=ev.event_id,
                            user_id=ev.user_id,
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

    async def stats(
        self,
        user_id: str,
        *,
        window_hours: int = 168,
    ) -> dict[str, Any]:
        """Aggregate stats for a user over a time window."""
        since = datetime.now(timezone.utc) - timedelta(hours=window_hours)
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
            if e.event_type in (EventType.EXERCISE_ATTEMPTED, EventType.EXERCISE_COMPLETED)
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
            async def __aenter__(self_):
                self_._s = store._sessionmaker()  # type: ignore[union-attr]
                return self_._s

            async def __aexit__(self_, exc_type, exc, tb):
                try:
                    if exc_type is None:
                        await self_._s.commit()
                    else:
                        await self_._s.rollback()
                finally:
                    await self_._s.close()

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
            data["created_at"] = datetime.now(timezone.utc).isoformat()
        elif isinstance(created_at, datetime):
            # Already a datetime — leave it (LearningEvent dataclass accepts datetime)
            pass
        elif isinstance(created_at, str):
            # Try parsing; if it fails, just use now
            try:
                datetime.fromisoformat(created_at)
            except (ValueError, TypeError):
                data["created_at"] = datetime.now(timezone.utc).isoformat()
        else:
            data["created_at"] = datetime.now(timezone.utc).isoformat()
        return LearningEvent.from_dict(data)


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
    "LearningEventStore",
    "get_learning_event_store",
    "reset_learning_event_store",
]
