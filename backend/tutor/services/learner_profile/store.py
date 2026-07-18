"""SQLite-backed profile store.

Why SQLite
----------
- One file, zero ops, perfect for single-user MVP.
- Async I/O via aiosqlite keeps us in the asyncio world.
- For multi-user production: swap the engine for Postgres (same SQLAlchemy
  async interface).

Schema
------
- ``profiles``     — current state (one row per user_id)
- ``profile_events`` — append-only audit log of every change (for diff
  replay, debugging, and explainability)

The store exposes a small async API. All "writes" go through :meth:`apply_diff`
so concurrent updates are merged deterministically.
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    select,
    text,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import Integer as SqlInteger

from tutor.services.config.settings import get_settings
from tutor.services.learner_profile.schema import (
    LearnerProfile,
    PersistedLearningPath,
    ProfileDiff,
    apply_diff,
    empty_profile,
)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""


class ProfileEventType(str, Enum):
    """All event types recorded in the audit log."""

    CREATED = "created"
    UPDATED = "updated"
    DIFF_APPLIED = "diff_applied"
    REPLACED = "replaced"
    DELETED = "deleted"


@dataclass
class ProfileEvent:
    """An audit-log entry. Lightweight value-object (not the ORM row)."""

    id: int | None
    user_id: str
    event_type: ProfileEventType
    payload: dict[str, Any]
    source: str
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "event_type": self.event_type.value,
            "payload": self.payload,
            "source": self.source,
            "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------


class ProfileRow(Base):
    """One row per user_id, holding the current profile as JSON."""

    __tablename__ = "profiles"

    user_id = Column(String(128), primary_key=True)
    version = Column(Integer, nullable=False, default=1)
    profile_data = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)


class ProfileEventRow(Base):
    """Append-only audit log."""

    __tablename__ = "profile_events"

    # SQLite requires INTEGER PRIMARY KEY for auto-rowid; use Integer
    # (BigInteger().with_variant(Integer, "sqlite") is the cross-DB idiom).
    id = Column(
        BigInteger().with_variant(SqlInteger, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    user_id = Column(String(128), nullable=False, index=True)
    event_type = Column(String(32), nullable=False)
    payload = Column(JSON, nullable=False)
    source = Column(String(64), nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_profile_events_user_time", "user_id", "created_at"),
    )


class LearningPathRow(Base):
    __tablename__ = "learning_paths"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(128), nullable=False, index=True)
    profile_version = Column(Integer, nullable=False)
    path_data = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)

    __table_args__ = (
        Index(
            "uq_learning_paths_user_profile_version",
            "user_id",
            "profile_version",
            unique=True,
        ),
    )


@dataclass(frozen=True)
class ProfileCasResult:
    profile: LearnerProfile
    applied: bool


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ProfileStore:
    """Async SQLite store for :class:`LearnerProfile` + audit log."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = get_settings().data_dir / "profiles.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None
        self._lock = threading.Lock()
        # Async lock for serialising the read-modify-write cycle inside one
        # process. SQLite has no row-level locking for our use case, so we
        # need this to prevent lost updates under concurrent apply_diff.
        self._write_lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Create tables (idempotent)."""
        engine = self._ensure_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info(f"ProfileStore ready at {self.db_path}")

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._sessionmaker = None

    def _ensure_engine(self) -> AsyncEngine:
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    url = f"sqlite+aiosqlite:///{self.db_path}"
                    self._engine = create_async_engine(
                        url,
                        echo=False,
                        future=True,
                        # SQLite + asyncio: serialize writes
                        connect_args={"check_same_thread": False},
                    )
                    self._sessionmaker = async_sessionmaker(
                        self._engine, expire_on_commit=False
                    )
                    # Lazily created event loop lock
                    self._write_lock = asyncio.Lock()
        return self._engine


class _SessionMixin:
    """Internal: helper for using sessions inside store methods."""

    _engine: AsyncEngine | None
    _sessionmaker: async_sessionmaker[AsyncSession] | None

    async def _with_session(self, fn):
        """Run ``fn(session)`` inside a fresh session, committing on success."""
        if self._sessionmaker is None:  # type: ignore[attr-defined]
            self._ensure_engine()  # type: ignore[attr-defined]
        assert self._sessionmaker is not None  # type: ignore[attr-defined]
        async with self._sessionmaker() as session:  # type: ignore[attr-defined]
            try:
                result = await fn(session)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise


# Attach mixin methods to ProfileStore
async def _get_or_create(self: ProfileStore, user_id: str) -> LearnerProfile:
    async def op(session: AsyncSession) -> LearnerProfile:
        row = await session.get(ProfileRow, user_id)
        if row is not None:
            data = dict(row.profile_data or {})
            # The indexed row owner is authoritative. Historical local-data
            # migrations may leave a stale owner inside the JSON snapshot.
            data["user_id"] = user_id
            data["version"] = int(row.version)
            return LearnerProfile.model_validate(data)
        # Create new
        now = datetime.now(UTC)
        prof = empty_profile(user_id=user_id)
        prof.created_at = now
        prof.updated_at = now
        prof.version = 1
        session.add(
            ProfileRow(
                user_id=user_id,
                version=prof.version,
                profile_data=prof.model_dump(mode="json"),
                created_at=prof.created_at,
                updated_at=prof.updated_at,
            )
        )
        session.add(
            ProfileEventRow(
                user_id=user_id,
                event_type=ProfileEventType.CREATED.value,
                payload={"version": prof.version},
                source="ProfileStore",
                created_at=now,
            )
        )
        return prof

    return await self._with_session(op)


async def _get(self: ProfileStore, user_id: str) -> LearnerProfile | None:
    async def op(session: AsyncSession) -> LearnerProfile | None:
        row = await session.get(ProfileRow, user_id)
        if row is None:
            return None
        data = dict(row.profile_data or {})
        data["user_id"] = user_id
        data["version"] = int(row.version)
        return LearnerProfile.model_validate(data)

    return await self._with_session(op)


async def _save_profile(
    self: ProfileStore,
    profile: LearnerProfile,
    source: str = "system",
    event_type: ProfileEventType = ProfileEventType.UPDATED,
) -> LearnerProfile:
    now = datetime.now(UTC)
    if profile.updated_at < now and event_type != ProfileEventType.CREATED:
        profile.updated_at = now

    async def op(session: AsyncSession) -> LearnerProfile:
        row = await session.get(ProfileRow, profile.user_id)
        payload = profile.model_dump(mode="json")
        if row is None:
            session.add(
                ProfileRow(
                    user_id=profile.user_id,
                    version=profile.version,
                    profile_data=payload,
                    created_at=profile.created_at,
                    updated_at=profile.updated_at,
                )
            )
        else:
            row.version = profile.version
            row.profile_data = payload
            row.updated_at = profile.updated_at
        session.add(
            ProfileEventRow(
                user_id=profile.user_id,
                event_type=event_type.value,
                payload={
                    "version": profile.version,
                    "summary": profile.to_summary(),
                },
                source=source,
                created_at=profile.updated_at,
            )
        )
        return profile

    return await self._with_session(op)


async def _apply_diff(
    self: ProfileStore,
    user_id: str,
    diff: ProfileDiff,
    *,
    source: str = "agent",
) -> LearnerProfile:
    """Load profile, apply diff, persist, log event.

    Serialised via ``self._write_lock`` so concurrent calls cannot lose
    updates (each call sees the previous call's write).
    """
    if diff.is_empty():
        return await _get_or_create(self, user_id)

    self._ensure_engine()
    assert self._write_lock is not None
    async with self._write_lock:
        profile = await _get_or_create(self, user_id)
        apply_diff(profile, diff)
        return await _save_profile(
            self,
            profile,
            source=source,
            event_type=ProfileEventType.DIFF_APPLIED,
        )


async def _save_event_profile(
    self: ProfileStore,
    candidate: LearnerProfile,
    *,
    expected_watermark: int,
) -> ProfileCasResult:
    """CAS a deterministic event window into the current profile."""
    self._ensure_engine()
    assert self._write_lock is not None
    async with self._write_lock, self._sessionmaker() as session:
        try:
            await session.execute(text("BEGIN IMMEDIATE"))
            row = await session.get(ProfileRow, candidate.user_id)
            if row is not None:
                current_data = dict(row.profile_data or {})
                current_data["user_id"] = candidate.user_id
                current_data["version"] = int(row.version)
                current = LearnerProfile.model_validate(current_data)
            else:
                current = empty_profile(candidate.user_id)
            if (
                current.event_watermark != expected_watermark
                or current.version != candidate.version
            ):
                await session.rollback()
                return ProfileCasResult(profile=current, applied=False)
            saved = candidate.model_copy(deep=True)
            saved.version = current.version + 1
            saved.created_at = current.created_at
            saved.updated_at = datetime.now(UTC)
            payload = saved.model_dump(mode="json")
            if row is None:
                session.add(
                    ProfileRow(
                        user_id=saved.user_id,
                        version=saved.version,
                        profile_data=payload,
                        created_at=saved.created_at,
                        updated_at=saved.updated_at,
                    )
                )
            else:
                row.version = saved.version
                row.profile_data = payload
                row.updated_at = saved.updated_at
            session.add(
                ProfileEventRow(
                    user_id=saved.user_id,
                    event_type=ProfileEventType.DIFF_APPLIED.value,
                    payload={"version": saved.version, "event_watermark": saved.event_watermark},
                    source="learning_events",
                    created_at=saved.updated_at,
                )
            )
            await session.commit()
            return ProfileCasResult(profile=saved, applied=True)
        except Exception:
            await session.rollback()
            raise


async def _save_path(
    self: ProfileStore, path: PersistedLearningPath
) -> PersistedLearningPath:
    self._ensure_engine()
    assert self._write_lock is not None
    assert self._sessionmaker is not None
    async with self._write_lock, self._sessionmaker() as session:
        try:
            await session.execute(text("BEGIN IMMEDIATE"))
            await session.execute(
                sqlite_insert(LearningPathRow)
                .values(
                    user_id=path.user_id,
                    profile_version=path.profile_version,
                    path_data=path.model_dump(mode="json"),
                    created_at=path.created_at,
                )
                .on_conflict_do_nothing(index_elements=["user_id", "profile_version"])
            )
            row = (
                await session.execute(
                    select(LearningPathRow).where(
                        LearningPathRow.user_id == path.user_id,
                        LearningPathRow.profile_version == path.profile_version,
                    )
                )
            ).scalar_one()
            await session.commit()
            data = dict(row.path_data or {})
            data["user_id"] = row.user_id
            data["profile_version"] = int(row.profile_version)
            return PersistedLearningPath.model_validate(data)
        except Exception:
            await session.rollback()
            raise


async def _get_path(
    self: ProfileStore, user_id: str, profile_version: int
) -> PersistedLearningPath | None:
    async def op(session: AsyncSession) -> PersistedLearningPath | None:
        row = (
            await session.execute(
                select(LearningPathRow).where(
                    LearningPathRow.user_id == user_id,
                    LearningPathRow.profile_version == profile_version,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        data = dict(row.path_data or {})
        data["user_id"] = row.user_id
        data["profile_version"] = int(row.profile_version)
        return PersistedLearningPath.model_validate(data)

    return await self._with_session(op)


async def _get_latest_path(
    self: ProfileStore, user_id: str
) -> PersistedLearningPath | None:
    async def op(session: AsyncSession) -> PersistedLearningPath | None:
        row = (
            await session.execute(
                select(LearningPathRow)
                .where(LearningPathRow.user_id == user_id)
                .order_by(LearningPathRow.profile_version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        data = dict(row.path_data or {})
        data["user_id"] = row.user_id
        data["profile_version"] = int(row.profile_version)
        return PersistedLearningPath.model_validate(data)

    return await self._with_session(op)


async def _replace(
    self: ProfileStore,
    profile: LearnerProfile,
    *,
    source: str = "system",
) -> LearnerProfile:
    """Hard-replace the stored profile (use sparingly).

    Caller is responsible for setting the version they want stored.
    """
    profile.updated_at = datetime.now(UTC)
    return await _save_profile(
        self,
        profile,
        source=source,
        event_type=ProfileEventType.REPLACED,
    )


async def _delete(self: ProfileStore, user_id: str) -> bool:
    async def op(session: AsyncSession) -> bool:
        row = await session.get(ProfileRow, user_id)
        if row is None:
            return False
        await session.delete(row)
        session.add(
            ProfileEventRow(
                user_id=user_id,
                event_type=ProfileEventType.DELETED.value,
                payload={},
                source="ProfileStore",
                created_at=datetime.now(UTC),
            )
        )
        return True

    return await self._with_session(op)


async def _history(
    self: ProfileStore,
    user_id: str,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[ProfileEvent]:
    async def op(session: AsyncSession) -> list[ProfileEvent]:
        stmt = (
            select(ProfileEventRow)
            .where(ProfileEventRow.user_id == user_id)
            .order_by(ProfileEventRow.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [
            ProfileEvent(
                id=r.id,
                user_id=r.user_id,
                event_type=ProfileEventType(r.event_type),
                payload=r.payload or {},
                source=r.source,
                created_at=r.created_at,
            )
            for r in rows
        ]

    return await self._with_session(op)


async def _list_users(self: ProfileStore) -> list[str]:
    async def op(session: AsyncSession) -> list[str]:
        stmt = select(ProfileRow.user_id).order_by(ProfileRow.updated_at.desc())
        return list((await session.execute(stmt)).scalars().all())

    return await self._with_session(op)


async def _stats(self: ProfileStore, user_id: str) -> dict[str, Any]:
    profile = await _get_or_create(self, user_id)
    history = await _history(self, user_id, limit=1)
    return {
        "summary": profile.to_summary(),
        "last_event": history[0].to_dict() if history else None,
        "event_count": len(history),
    }


# Bind methods to ProfileStore
ProfileStore.get_or_create = _get_or_create  # type: ignore[attr-defined]
ProfileStore.get = _get  # type: ignore[attr-defined]
ProfileStore.save = _save_profile  # type: ignore[attr-defined]
ProfileStore.apply_diff = _apply_diff  # type: ignore[attr-defined]
ProfileStore.save_event_profile = _save_event_profile  # type: ignore[attr-defined]
ProfileStore.save_path = _save_path  # type: ignore[attr-defined]
ProfileStore.get_path = _get_path  # type: ignore[attr-defined]
ProfileStore.get_latest_path = _get_latest_path  # type: ignore[attr-defined]
ProfileStore.replace = _replace  # type: ignore[attr-defined]
ProfileStore.delete = _delete  # type: ignore[attr-defined]
ProfileStore.history = _history  # type: ignore[attr-defined]
ProfileStore.list_users = _list_users  # type: ignore[attr-defined]
ProfileStore.stats = _stats  # type: ignore[attr-defined]
ProfileStore._with_session = _SessionMixin._with_session  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------


_store: ProfileStore | None = None
_store_lock = threading.Lock()


def get_profile_store() -> ProfileStore:
    """Return the singleton :class:`ProfileStore` (initialised lazily).

    The store is *not* connected to the DB until :meth:`init` is called
    (typically from the FastAPI lifespan handler).
    """
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = ProfileStore()
    return _store


async def reset_profile_store() -> None:
    """Close and clear the singleton. Used by tests."""
    global _store
    if _store is not None:
        await _store.close()
    _store = None


def _close_profile_store_sync() -> None:
    """Synchronous variant for test fixtures that can't await.

    The old code called the async ``reset_profile_store()`` without
    ``await``, returning a coroutine that was never scheduled. The
    store singleton kept its reference to the previous test's temp
    directory, which the conftest had already deleted by the time
    the next test ran — that caused "unable to open database file".
    """
    global _store
    if _store is None:
        return
    store = _store
    _store = None
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(store.close())
    else:
        running_loop.create_task(store.close())


__all__ = [
    "ProfileEvent",
    "ProfileEventType",
    "ProfileCasResult",
    "ProfileStore",
    "get_profile_store",
    "reset_profile_store",
    "_close_profile_store_sync",
]
