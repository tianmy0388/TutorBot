"""JobStore — SQLite-backed persistence for async jobs.

Mirrors the design of :class:`LearningEventStore` and
:class:`ResourcePackageStore`: SQLAlchemy 2.0 async + aiosqlite,
``BigInteger().with_variant(Integer, "sqlite")`` for portability, a
singleton accessor with thread-lock, and ``_with_session()`` for
per-call transactions.

One table, ``jobs``. The full state — including the replayed event
buffer and the final result payload — lives in JSON columns. This
trades schema-rigour for the ability to evolve capability outputs
without DB migrations.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Integer,
    String,
    select,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import Integer as SqlInteger

from tutor.services.config.settings import get_settings
from tutor.services.jobs.schema import Job, JobStatus
from tutor.services.resource_package.schema import ArtifactRef


class _Base(DeclarativeBase):
    pass


class JobRow(_Base):
    """One persisted Job."""

    __tablename__ = "jobs"

    id = Column(
        BigInteger().with_variant(SqlInteger, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    job_id = Column(String(64), nullable=False, unique=True)
    user_id = Column(String(128), nullable=False, index=True)
    session_id = Column(String(64), nullable=False, default="")
    capability = Column(String(64), nullable=False, default="resource_generation")
    status = Column(String(32), nullable=False, default=JobStatus.PENDING.value, index=True)

    # Inputs
    message = Column(String, nullable=False, default="")
    language = Column(String(8), nullable=False, default="zh")
    metadata_json = Column(JSON, nullable=False, default=dict)

    # Lifecycle
    error = Column(String, nullable=True)
    error_log_ref = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    # Output
    result = Column(JSON, nullable=True)
    event_count = Column(Integer, nullable=False, default=0)
    last_seq = Column(Integer, nullable=False, default=0)

    # Replay buffer (capped — see JobRunner)
    events = Column(JSON, nullable=False, default=list)

    __table_args__ = ()


class JobStore:
    """Async SQLite store for :class:`Job`."""

    # Maximum number of serialized events we keep per job. Older events
    # are dropped FIFO once we exceed this; subscribers that connect
    # after this point only see the live stream.
    MAX_EVENTS_PER_JOB = 200

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            db_path = get_settings().data_dir / "jobs.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None
        self._write_lock: asyncio.Lock | None = None
        self._lock = threading.Lock()

    # ---- lifecycle --------------------------------------------------------

    async def init(self) -> None:
        engine = self._ensure_engine()
        async with engine.begin() as conn:
            await conn.run_sync(_Base.metadata.create_all)
            columns = {
                str(row[1])
                for row in (
                    await conn.exec_driver_sql("PRAGMA table_info(jobs)")
                ).fetchall()
            }
            if "error_log_ref" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN error_log_ref JSON"
                )
        logger.info(f"JobStore ready at {self.db_path}")

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

    def _with_session(self):
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

    # ---- writes -----------------------------------------------------------

    async def save(self, job: Job) -> Job:
        """Insert-or-replace a job row."""
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            existing = await session.execute(
                select(JobRow).where(JobRow.job_id == job.job_id)
            )
            existing_row = existing.scalar_one_or_none()
            if existing_row is not None:
                await session.delete(existing_row)
                await session.flush()
            session.add(self._to_row(job))
        return job

    async def update_status(
        self,
        job_id: str,
        *,
        status: JobStatus,
        error: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        """Patch the lifecycle fields of a job."""
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            row = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == job_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return
            row.status = status.value
            if error is not None:
                row.error = error
            if started_at is not None:
                row.started_at = started_at
            if finished_at is not None:
                row.finished_at = finished_at
            if result is not None:
                row.result = result

    async def set_terminal(
        self,
        job_id: str,
        *,
        status: JobStatus,
        finished_at: datetime | None,
        result: dict[str, Any],
        terminal_event: dict[str, Any],
        error: str | None = None,
        error_log_ref: ArtifactRef | None = None,
    ) -> bool:
        """Atomically persist a job's one terminal transition.

        The replay buffer is the durable idempotency guard.  Status, public
        result, diagnostic reference and ``job_terminal`` are committed under
        the same write lock and database transaction, so a retry cannot append
        a second terminal event or replace the first outcome.
        """
        terminal_statuses = {
            JobStatus.SUCCEEDED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
        if status not in terminal_statuses:
            raise ValueError(f"set_terminal requires terminal status, got {status}")

        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            row = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == job_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            events: list[dict[str, Any]] = list(row.events or [])
            if any(event.get("type") == "job_terminal" for event in events):
                return False

            events.append(terminal_event)
            if len(events) > self.MAX_EVENTS_PER_JOB:
                events = events[-self.MAX_EVENTS_PER_JOB :]
            row.events = events
            row.event_count = (row.event_count or 0) + 1
            row.last_seq = int(terminal_event.get("seq") or row.last_seq or 0)
            row.status = status.value
            row.finished_at = finished_at
            row.result = result
            row.error = error
            row.error_log_ref = (
                error_log_ref.model_dump(mode="json")
                if error_log_ref is not None
                else None
            )
            return True

    async def append_event(
        self,
        job_id: str,
        event_dict: dict[str, Any],
        last_seq: int,
    ) -> None:
        """Append one event to the replay buffer + bump counters.

        Older events are dropped once we exceed ``MAX_EVENTS_PER_JOB``.
        """
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            row = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == job_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return
            buf: list[dict[str, Any]] = list(row.events or [])
            buf.append(event_dict)
            if len(buf) > self.MAX_EVENTS_PER_JOB:
                buf = buf[-self.MAX_EVENTS_PER_JOB :]
            row.events = buf
            row.event_count = (row.event_count or 0) + 1
            row.last_seq = last_seq

    async def delete(self, job_id: str) -> bool:
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            row = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == job_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
        return True

    async def delete_user(self, user_id: str) -> int:
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            rows = (
                await session.execute(
                    select(JobRow).where(JobRow.user_id == user_id)
                )
            ).scalars().all()
            count = len(rows)
            for r in rows:
                await session.delete(r)
        return count

    # ---- reads ------------------------------------------------------------

    async def get(self, job_id: str) -> Job | None:
        self._ensure_engine()
        async with self._with_session() as session:
            row = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == job_id)
                )
            ).scalar_one_or_none()
            return self._row_to_job(row) if row else None

    async def list(
        self,
        user_id: str,
        *,
        status: JobStatus | None = None,
        limit: int = 50,
        offset: int = 0,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(JobRow).where(JobRow.user_id == user_id)
            if status is not None:
                stmt = stmt.where(JobRow.status == status.value)
            # 2026-06-21 plan: filter by session_id so conversation
            # detail can return only the jobs that belong to a single
            # conversation. Empty string (the column default) is
            # treated as "no session assigned" and excluded by default.
            if session_id is not None:
                stmt = stmt.where(JobRow.session_id == session_id)
            stmt = stmt.order_by(JobRow.created_at.desc()).limit(limit).offset(offset)
            rows = (await session.execute(stmt)).scalars().all()
            return [self._row_to_job(r).to_summary() for r in rows if r is not None]

    async def list_for_session(
        self,
        session_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List a conversation's jobs after ownership was checked upstream."""
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = (
                select(JobRow)
                .where(JobRow.session_id == session_id)
                .order_by(JobRow.created_at.desc(), JobRow.id.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [self._row_to_job(row).to_summary() for row in reversed(rows)]

    async def count(
        self,
        user_id: str,
        *,
        status: JobStatus | None = None,
    ) -> int:
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(JobRow).where(JobRow.user_id == user_id)
            if status is not None:
                stmt = stmt.where(JobRow.status == status.value)
            rows = (await session.execute(stmt)).scalars().all()
            return len(rows)

    async def list_active(self, user_id: str | None = None) -> list[Job]:
        """Return all PENDING or RUNNING jobs (optionally filtered by user)."""
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(JobRow).where(
                JobRow.status.in_([JobStatus.PENDING.value, JobStatus.RUNNING.value])
            )
            if user_id is not None:
                stmt = stmt.where(JobRow.user_id == user_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [self._row_to_job(r) for r in rows if r is not None]

    async def stats(self, user_id: str) -> dict[str, Any]:
        self._ensure_engine()
        async with self._with_session() as session:
            rows = (
                await session.execute(
                    select(JobRow).where(JobRow.user_id == user_id)
                )
            ).scalars().all()

        if not rows:
            return {
                "job_count": 0,
                "active_count": 0,
                "by_status": {},
                "by_capability": {},
                "first_at": None,
                "last_at": None,
            }

        by_status: dict[str, int] = {}
        by_cap: dict[str, int] = {}
        active = 0
        for r in rows:
            by_status[r.status] = by_status.get(r.status, 0) + 1
            by_cap[r.capability] = by_cap.get(r.capability, 0) + 1
            if r.status in (JobStatus.PENDING.value, JobStatus.RUNNING.value):
                active += 1

        return {
            "job_count": len(rows),
            "active_count": active,
            "by_status": by_status,
            "by_capability": by_cap,
            "first_at": min(r.created_at for r in rows).isoformat(),
            "last_at": max(r.created_at for r in rows).isoformat(),
        }

    # ---- row <-> model ----------------------------------------------------

    @staticmethod
    def _to_row(job: Job) -> JobRow:
        return JobRow(
            job_id=job.job_id,
            user_id=job.user_id,
            session_id=job.session_id or "",
            capability=job.capability,
            status=job.status.value,
            message=job.message or "",
            language=job.language,
            metadata_json=dict(job.metadata or {}),
            error=job.error,
            error_log_ref=(
                job.error_log_ref.model_dump(mode="json")
                if job.error_log_ref is not None
                else None
            ),
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            result=job.result,
            event_count=job.event_count,
            last_seq=job.last_seq,
            events=list(job.events or []),
        )

    @staticmethod
    def _row_to_job(row: JobRow) -> Job:
        # Legacy "completed" rows (pre-Phase 5.2) hydrate as SUCCEEDED so
        # existing jobs.db files stay readable without a migration.
        try:
            status = JobStatus(row.status)
        except ValueError:
            if row.status == "completed":
                status = JobStatus.SUCCEEDED
            else:
                raise
        return Job(
            job_id=row.job_id,
            user_id=row.user_id,
            session_id=row.session_id,
            capability=row.capability,
            status=status,
            message=row.message or "",
            language=row.language or "zh",
            metadata=dict(row.metadata_json or {}),
            error=row.error,
            error_log_ref=(
                ArtifactRef.model_validate(row.error_log_ref)
                if row.error_log_ref
                else None
            ),
            created_at=row.created_at or datetime.now(UTC),
            started_at=row.started_at,
            finished_at=row.finished_at,
            result=dict(row.result) if row.result else None,
            event_count=row.event_count or 0,
            last_seq=row.last_seq or 0,
            events=list(row.events or []),
        )


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


_store: JobStore | None = None
_store_lock = threading.Lock()


def get_job_store() -> JobStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = JobStore()
                logger.info("JobStore singleton created")
    return _store


def reset_job_store() -> None:
    global _store
    _store = None


__all__ = ["JobStore", "get_job_store", "reset_job_store"]
