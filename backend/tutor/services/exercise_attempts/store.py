"""Application-owned SQLite store for terminal code attempts."""

from __future__ import annotations

import asyncio
import hashlib
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import Integer as SqlInteger

from tutor.services.exercise_attempts.schema import ExerciseAttempt


class AttemptConflictError(ValueError):
    code = "ATTEMPT_ID_CONFLICT"


class AttemptOwnershipError(ValueError):
    code = "ATTEMPT_NOT_FOUND"


@dataclass(frozen=True)
class AttemptClaim:
    attempt_id: str
    acquired: bool


class _Base(DeclarativeBase):
    pass


class AttemptRow(_Base):
    __tablename__ = "exercise_attempts"

    id = Column(
        BigInteger().with_variant(SqlInteger, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    attempt_id = Column(String(64), nullable=False, unique=True)
    client_attempt_id = Column(String(64), nullable=True, unique=True)
    user_id = Column(String(128), nullable=False, index=True)
    session_id = Column(String(64), nullable=False, default="")
    package_id = Column(String(64), nullable=False, index=True)
    question_id = Column(String(64), nullable=False, index=True)
    concept_id = Column(String(128), nullable=False, default="")
    course = Column(String(128), nullable=False, default="")
    source_code = Column(Text, nullable=False)
    status = Column(String(32), nullable=False)
    passed_tests = Column(Integer, nullable=False)
    total_tests = Column(Integer, nullable=False)
    test_results = Column(JSON, nullable=False)
    stdout = Column(Text, nullable=False, default="")
    stderr = Column(Text, nullable=False, default="")
    duration_seconds = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
    event_published = Column(Boolean, nullable=False, default=False)
    error_code = Column(String(64), nullable=True)

    __table_args__ = (
        Index(
            "ix_attempts_owner_question_time",
            "user_id",
            "package_id",
            "question_id",
            "created_at",
        ),
    )


class AttemptClaimRow(_Base):
    __tablename__ = "exercise_attempt_claims"

    client_attempt_id = Column(String(64), primary_key=True)
    attempt_id = Column(String(64), nullable=False, unique=True)
    user_id = Column(String(128), nullable=False)
    package_id = Column(String(64), nullable=False)
    question_id = Column(String(64), nullable=False)
    source_hash = Column(String(64), nullable=False)
    claimed_at = Column(DateTime(timezone=True), nullable=False)


class ExerciseAttemptStore:
    """Persist every terminal result and publication watermark."""

    def __init__(
        self, db_path: str | Path, *, claim_lease_seconds: int = 30
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None
        self._write_lock: asyncio.Lock | None = None
        self._lock = threading.Lock()
        self._claim_lease_seconds = max(0, claim_lease_seconds)

    async def init(self) -> None:
        engine = self._ensure_engine()
        async with engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
            columns = {
                str(row[1])
                for row in (
                    await conn.exec_driver_sql("PRAGMA table_info(exercise_attempts)")
                ).fetchall()
            }
            if "event_published" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE exercise_attempts "
                    "ADD COLUMN event_published INTEGER NOT NULL DEFAULT 0"
                )
            if "error_code" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE exercise_attempts ADD COLUMN error_code VARCHAR(64)"
                )

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
                    self._engine = create_async_engine(
                        f"sqlite+aiosqlite:///{self.db_path}",
                        future=True,
                        connect_args={"check_same_thread": False},
                    )
                    self._sessionmaker = async_sessionmaker(
                        self._engine, expire_on_commit=False
                    )
                    self._write_lock = asyncio.Lock()
        return self._engine

    def _with_session(self):
        if self._sessionmaker is None:
            self._ensure_engine()
        assert self._sessionmaker is not None
        store = self

        class _Ctx:
            async def __aenter__(self):
                self.session = store._sessionmaker()  # type: ignore[union-attr]
                return self.session

            async def __aexit__(self, exc_type, exc, tb):
                try:
                    if exc_type is None:
                        await self.session.commit()
                    else:
                        await self.session.rollback()
                finally:
                    await self.session.close()

        return _Ctx()

    async def save_terminal(self, attempt: ExerciseAttempt) -> ExerciseAttempt:
        """Insert once; equal client-id retries return the durable row."""
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            if attempt.client_attempt_id:
                existing = (
                    await session.execute(
                        select(AttemptRow).where(
                            AttemptRow.client_attempt_id == attempt.client_attempt_id
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    if existing.user_id != attempt.user_id:
                        raise AttemptOwnershipError()
                    persisted = self._row_to_attempt(existing)
                    if self._idempotency_key(persisted) != self._idempotency_key(attempt):
                        raise AttemptConflictError()
                    return persisted

            existing_id = (
                await session.execute(
                    select(AttemptRow).where(AttemptRow.attempt_id == attempt.attempt_id)
                )
            ).scalar_one_or_none()
            if existing_id is not None:
                persisted = self._row_to_attempt(existing_id)
                if persisted.user_id != attempt.user_id:
                    raise AttemptOwnershipError()
                if self._idempotency_key(persisted) != self._idempotency_key(attempt):
                    raise AttemptConflictError()
                return persisted

            session.add(
                AttemptRow(
                    attempt_id=attempt.attempt_id,
                    client_attempt_id=attempt.client_attempt_id,
                    user_id=attempt.user_id,
                    session_id=attempt.session_id,
                    package_id=attempt.package_id,
                    question_id=attempt.question_id,
                    concept_id=attempt.concept_id,
                    course=attempt.course,
                    source_code=attempt.source_code,
                    status=attempt.status.value,
                    passed_tests=attempt.passed_tests,
                    total_tests=attempt.total_tests,
                    test_results=[
                        item.model_dump(mode="json", exclude_none=True)
                        for item in attempt.test_results
                    ],
                    stdout=attempt.stdout,
                    stderr=attempt.stderr,
                    duration_seconds=attempt.duration_seconds,
                    created_at=attempt.created_at,
                    event_published=attempt.event_published,
                    error_code=attempt.error_code,
                )
            )
        return attempt

    async def claim_attempt(
        self,
        *,
        client_attempt_id: str,
        user_id: str,
        package_id: str,
        question_id: str,
        source_code: str,
    ) -> AttemptClaim:
        """Durably elect one executor for a caller-supplied idempotency id."""
        self._ensure_engine()
        assert self._write_lock is not None
        source_hash = hashlib.sha256(source_code.encode("utf-8")).hexdigest()
        now = datetime.now(UTC)
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            row = (
                await session.execute(
                    select(AttemptClaimRow).where(
                        AttemptClaimRow.client_attempt_id == client_attempt_id
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                if row.user_id != user_id:
                    raise AttemptOwnershipError()
                if (
                    row.package_id,
                    row.question_id,
                    row.source_hash,
                ) != (package_id, question_id, source_hash):
                    raise AttemptConflictError()
                terminal = (
                    await session.execute(
                        select(AttemptRow.attempt_id).where(
                            AttemptRow.attempt_id == row.attempt_id
                        )
                    )
                ).scalar_one_or_none()
                claimed_at = row.claimed_at
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=UTC)
                if terminal is None and claimed_at <= now - timedelta(
                    seconds=self._claim_lease_seconds
                ):
                    row.claimed_at = now
                    return AttemptClaim(row.attempt_id, True)
                return AttemptClaim(row.attempt_id, False)

            attempt_id = uuid.uuid4().hex
            session.add(
                AttemptClaimRow(
                    client_attempt_id=client_attempt_id,
                    attempt_id=attempt_id,
                    user_id=user_id,
                    package_id=package_id,
                    question_id=question_id,
                    source_hash=source_hash,
                    claimed_at=now,
                )
            )
            return AttemptClaim(attempt_id, True)

    async def reap_orphaned_claims(self) -> int:
        """Drop non-terminal claims during startup, before requests can run."""
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            rows = (await session.execute(select(AttemptClaimRow))).scalars().all()
            removed = 0
            for row in rows:
                terminal = (
                    await session.execute(
                        select(AttemptRow.attempt_id).where(
                            AttemptRow.attempt_id == row.attempt_id
                        )
                    )
                ).scalar_one_or_none()
                if terminal is None:
                    await session.delete(row)
                    removed += 1
            return removed

    async def get_for_user(
        self, attempt_id: str, user_id: str
    ) -> ExerciseAttempt | None:
        self._ensure_engine()
        async with self._with_session() as session:
            row = (
                await session.execute(
                    select(AttemptRow).where(
                        AttemptRow.attempt_id == attempt_id,
                        AttemptRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            return self._row_to_attempt(row) if row is not None else None

    async def get_by_client_id(
        self, client_attempt_id: str, user_id: str
    ) -> ExerciseAttempt | None:
        self._ensure_engine()
        async with self._with_session() as session:
            row = (
                await session.execute(
                    select(AttemptRow).where(
                        AttemptRow.client_attempt_id == client_attempt_id
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.user_id != user_id:
                raise AttemptOwnershipError()
            return self._row_to_attempt(row)

    async def list_attempts(
        self,
        user_id: str,
        package_id: str,
        question_id: str,
        *,
        limit: int,
        offset: int,
    ) -> list[ExerciseAttempt]:
        self._ensure_engine()
        async with self._with_session() as session:
            rows = (
                await session.execute(
                    select(AttemptRow)
                    .where(
                        AttemptRow.user_id == user_id,
                        AttemptRow.package_id == package_id,
                        AttemptRow.question_id == question_id,
                    )
                    .order_by(AttemptRow.created_at.desc(), AttemptRow.id.desc())
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars().all()
            return [self._row_to_attempt(row) for row in rows]

    async def count_attempts(
        self,
        user_id: str,
        package_id: str,
        question_id: str,
    ) -> int:
        """Return the owner-scoped total independently of page bounds."""
        self._ensure_engine()
        async with self._with_session() as session:
            total = await session.scalar(
                select(func.count(AttemptRow.id)).where(
                    AttemptRow.user_id == user_id,
                    AttemptRow.package_id == package_id,
                    AttemptRow.question_id == question_id,
                )
            )
            return int(total or 0)

    async def list_unpublished(self, *, limit: int = 1000) -> list[ExerciseAttempt]:
        self._ensure_engine()
        async with self._with_session() as session:
            rows = (
                await session.execute(
                    select(AttemptRow)
                    .where(AttemptRow.event_published.is_(False))
                    .order_by(AttemptRow.created_at, AttemptRow.id)
                    .limit(limit)
                )
            ).scalars().all()
            return [self._row_to_attempt(row) for row in rows]

    async def mark_event_published(self, attempt_id: str, user_id: str) -> bool:
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            row = (
                await session.execute(
                    select(AttemptRow).where(
                        AttemptRow.attempt_id == attempt_id,
                        AttemptRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            row.event_published = True
            return True

    @staticmethod
    def _idempotency_key(attempt: ExerciseAttempt) -> tuple[str, str, str, str]:
        return (
            attempt.user_id,
            attempt.package_id,
            attempt.question_id,
            attempt.source_code,
        )

    @staticmethod
    def _row_to_attempt(row: AttemptRow) -> ExerciseAttempt:
        return ExerciseAttempt.model_validate(
            {
                "attempt_id": row.attempt_id,
                "client_attempt_id": row.client_attempt_id,
                "user_id": row.user_id,
                "session_id": row.session_id or "",
                "package_id": row.package_id,
                "question_id": row.question_id,
                "concept_id": row.concept_id or "",
                "course": row.course or "",
                "source_code": row.source_code,
                "status": row.status,
                "passed_tests": row.passed_tests,
                "total_tests": row.total_tests,
                "test_results": list(row.test_results or []),
                "stdout": row.stdout or "",
                "stderr": row.stderr or "",
                "duration_seconds": row.duration_seconds or 0.0,
                "created_at": row.created_at,
                "event_published": bool(row.event_published),
                "error_code": row.error_code,
            }
        )


__all__ = [
    "AttemptClaim",
    "AttemptConflictError",
    "AttemptOwnershipError",
    "ExerciseAttemptStore",
]
