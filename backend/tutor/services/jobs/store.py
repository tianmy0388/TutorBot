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
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
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
    AsyncConnection,
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

_MIGRATION_STATUS_PRIORITY = {
    JobStatus.SUCCEEDED.value: 0,
    "completed": 0,
    JobStatus.PARTIAL.value: 1,
    JobStatus.RUNNING.value: 2,
    JobStatus.PENDING.value: 3,
    JobStatus.FAILED.value: 4,
    JobStatus.CANCELLED.value: 5,
}


def _migration_job_key(row: Any) -> tuple[int, int]:
    return (
        _MIGRATION_STATUS_PRIORITY.get(str(row["status"]), 99),
        int(row["id"]),
    )


async def _merge_duplicate_job_tree(
    conn: AsyncConnection,
    *,
    canonical_job_id: str,
    duplicate_job_id: str,
) -> None:
    """Move/merge every child before deleting a legacy duplicate job."""
    children = (
        await conn.exec_driver_sql(
            "SELECT id, job_id, status, dedupe_key FROM jobs "
            "WHERE parent_job_id = ? ORDER BY id",
            (duplicate_job_id,),
        )
    ).mappings().all()
    for child in children:
        dedupe_key = child["dedupe_key"]
        if dedupe_key is None:
            await conn.exec_driver_sql(
                "UPDATE jobs SET parent_job_id = ? WHERE job_id = ?",
                (canonical_job_id, child["job_id"]),
            )
            continue
        existing = (
            await conn.exec_driver_sql(
                "SELECT id, job_id, status, dedupe_key FROM jobs "
                "WHERE parent_job_id = ? AND dedupe_key = ? AND job_id <> ?",
                (canonical_job_id, dedupe_key, child["job_id"]),
            )
        ).mappings().first()
        if existing is None:
            await conn.exec_driver_sql(
                "UPDATE jobs SET parent_job_id = ? WHERE job_id = ?",
                (canonical_job_id, child["job_id"]),
            )
            continue

        winner, loser = sorted((existing, child), key=_migration_job_key)
        await _merge_duplicate_job_tree(
            conn,
            canonical_job_id=str(winner["job_id"]),
            duplicate_job_id=str(loser["job_id"]),
        )
        await conn.exec_driver_sql(
            "DELETE FROM jobs WHERE job_id = ?",
            (loser["job_id"],),
        )
        if winner["job_id"] == child["job_id"]:
            await conn.exec_driver_sql(
                "UPDATE jobs SET parent_job_id = ? WHERE job_id = ?",
                (canonical_job_id, winner["job_id"]),
            )


async def _migrate_learning_child_duplicates(conn: AsyncConnection) -> None:
    rows = (
        await conn.exec_driver_sql(
            "SELECT id, job_id, user_id, task_kind, dedupe_key, status "
            "FROM jobs WHERE task_kind IN ('profile_update', 'path_rebuild') "
            "AND dedupe_key IS NOT NULL ORDER BY id"
        )
    ).mappings().all()
    grouped: dict[tuple[str, str, str], list[Any]] = {}
    for row in rows:
        key = (str(row["user_id"]), str(row["task_kind"]), str(row["dedupe_key"]))
        grouped.setdefault(key, []).append(row)
    for duplicates in grouped.values():
        if len(duplicates) < 2:
            continue
        canonical, *discarded = sorted(duplicates, key=_migration_job_key)
        for duplicate in discarded:
            await _merge_duplicate_job_tree(
                conn,
                canonical_job_id=str(canonical["job_id"]),
                duplicate_job_id=str(duplicate["job_id"]),
            )
            await conn.exec_driver_sql(
                "DELETE FROM jobs WHERE job_id = ?",
                (duplicate["job_id"],),
            )


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
    parent_job_id = Column(String(64), nullable=True, index=True)
    task_kind = Column(String(64), nullable=True)
    dedupe_key = Column(String(256), nullable=True)
    claim_owner = Column(String(64), nullable=True)
    claim_expires_at = Column(DateTime(timezone=True), nullable=True)
    claim_generation = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default=JobStatus.PENDING.value, index=True)

    # Inputs
    message = Column(String, nullable=False, default="")
    language = Column(String(8), nullable=False, default="zh")
    metadata_json = Column(JSON, nullable=False, default=dict)
    web_search_enabled = Column(Boolean, nullable=False, default=False)

    # Lifecycle
    error = Column(String, nullable=True)
    error_log_ref = Column(JSON, nullable=True)
    terminal_event_id = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    # Output
    result = Column(JSON, nullable=True)
    event_count = Column(Integer, nullable=False, default=0)
    last_seq = Column(Integer, nullable=False, default=0)

    # Replay buffer (capped — see JobRunner)
    events = Column(JSON, nullable=False, default=list)

    __table_args__ = (
        Index(
            "uq_jobs_parent_dedupe",
            "parent_job_id",
            "dedupe_key",
            unique=True,
        ),
    )


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
            if "terminal_event_id" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN terminal_event_id VARCHAR(64)"
                )
            if "parent_job_id" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN parent_job_id VARCHAR(64)"
                )
            if "task_kind" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN task_kind VARCHAR(64)"
                )
            if "dedupe_key" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN dedupe_key VARCHAR(256)"
                )
            if "claim_owner" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN claim_owner VARCHAR(64)"
                )
            if "claim_expires_at" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN claim_expires_at DATETIME"
                )
            if "claim_generation" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN claim_generation INTEGER NOT NULL DEFAULT 0"
                )
            if "web_search_enabled" not in columns:
                await conn.exec_driver_sql(
                    "ALTER TABLE jobs ADD COLUMN "
                    "web_search_enabled BOOLEAN NOT NULL DEFAULT 0"
                )
            await conn.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_jobs_parent_job_id "
                "ON jobs (parent_job_id)"
            )
            await conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_parent_dedupe "
                "ON jobs (parent_job_id, dedupe_key)"
            )
            # Preserve the best durable job and recursively re-parent/merge
            # descendants before enforcing the new cross-parent uniqueness.
            await _migrate_learning_child_duplicates(conn)
            await conn.exec_driver_sql(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_learning_follow_up_dedupe "
                "ON jobs (user_id, task_kind, dedupe_key) "
                "WHERE task_kind IN ('profile_update', 'path_rebuild')"
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

    async def ensure_parent(self, job: Job) -> Job:
        """Atomically create a deterministic root job without replacing it."""
        if job.parent_job_id is not None:
            raise ValueError("ensure_parent requires a root job")
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            row = self._to_row(job)
            values = {
                column.name: getattr(row, column.name)
                for column in JobRow.__table__.columns
                if column.name != "id"
            }
            await session.execute(
                sqlite_insert(JobRow)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["job_id"])
            )
            persisted = (
                await session.execute(select(JobRow).where(JobRow.job_id == job.job_id))
            ).scalar_one()
            return self._row_to_job(persisted)

    async def create_child_if_absent(
        self,
        *,
        parent_job_id: str,
        task_kind: str,
        dedupe_key: str,
        payload: dict[str, Any],
    ) -> Job:
        """Create one queued child, or return the durable existing child."""
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            parent = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == parent_job_id)
                )
            ).scalar_one_or_none()
            if parent is None:
                raise KeyError(f"parent job not found: {parent_job_id}")
            child = Job(
                user_id=parent.user_id,
                session_id=parent.session_id,
                capability=task_kind,
                parent_job_id=parent_job_id,
                task_kind=task_kind,
                dedupe_key=dedupe_key,
                message=parent.message or "",
                language=parent.language or "zh",
                metadata=dict(payload),
                web_search_enabled=bool(parent.web_search_enabled),
                status=JobStatus.PENDING,
            )
            child_row = self._to_row(child)
            values = {
                column.name: getattr(child_row, column.name)
                for column in JobRow.__table__.columns
                if column.name != "id"
            }
            await session.execute(
                sqlite_insert(JobRow)
                .values(**values)
                .on_conflict_do_nothing()
            )
            if task_kind in {"profile_update", "path_rebuild"}:
                predicate = (
                    JobRow.user_id == parent.user_id,
                    JobRow.task_kind == task_kind,
                    JobRow.dedupe_key == dedupe_key,
                )
            else:
                predicate = (
                    JobRow.parent_job_id == parent_job_id,
                    JobRow.dedupe_key == dedupe_key,
                )
            row = (await session.execute(select(JobRow).where(*predicate))).scalar_one()
            return self._row_to_job(row)

    async def create_child_if_absent_with_bind(
        self,
        *,
        parent_job_id: str,
        task_kind: str,
        dedupe_key: str,
        payload: dict[str, Any],
        bind: Callable[[Job], Awaitable[bool]],
    ) -> Job | None:
        """Insert a child and externally bind it before either becomes claimable.

        The jobs ``BEGIN IMMEDIATE`` transaction remains open while ``bind``
        performs the resource CAS. Competing runners cannot claim the
        uncommitted child. A false bind deletes it before commit.
        """
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            parent = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == parent_job_id)
                )
            ).scalar_one_or_none()
            if parent is None:
                raise KeyError(f"parent job not found: {parent_job_id}")
            child = Job(
                user_id=parent.user_id,
                session_id=parent.session_id,
                capability=task_kind,
                parent_job_id=parent_job_id,
                task_kind=task_kind,
                dedupe_key=dedupe_key,
                message=parent.message or "",
                language=parent.language or "zh",
                metadata=dict(payload),
                web_search_enabled=bool(parent.web_search_enabled),
                status=JobStatus.PENDING,
            )
            child_row = self._to_row(child)
            values = {
                column.name: getattr(child_row, column.name)
                for column in JobRow.__table__.columns
                if column.name != "id"
            }
            await session.execute(
                sqlite_insert(JobRow).values(**values).on_conflict_do_nothing()
            )
            predicate = (
                JobRow.parent_job_id == parent_job_id,
                JobRow.dedupe_key == dedupe_key,
            )
            row = (
                await session.execute(select(JobRow).where(*predicate))
            ).scalar_one()
            durable = self._row_to_job(row)
            if durable.status not in {JobStatus.PENDING, JobStatus.RUNNING}:
                return durable
            if await bind(durable):
                return durable
            await session.delete(row)
            return None

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
        terminal_event: dict[str, Any] | None = None,
        terminal_events: list[dict[str, Any]] | None = None,
        error: str | None = None,
        error_log_ref: ArtifactRef | None = None,
        expected_claim_owner: str | None = None,
        expected_claim_generation: int | None = None,
    ) -> bool:
        """Atomically persist a job's one terminal transition.

        Status, public result, diagnostic reference and the complete canonical
        terminal event bundle are committed under one write lock/database
        transaction.  ``terminal_event_id`` (or an already-finished legacy
        row) is the durable idempotency guard; the capped replay buffer is not.
        """
        terminal_statuses = {
            JobStatus.SUCCEEDED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }
        if status not in terminal_statuses:
            raise ValueError(f"set_terminal requires terminal status, got {status}")
        bundle = list(terminal_events or ())
        if terminal_event is not None:
            bundle.append(terminal_event)
        terminal_items = [event for event in bundle if event.get("type") == "job_terminal"]
        if len(terminal_items) != 1:
            raise ValueError("terminal bundle requires exactly one job_terminal event")

        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            # Each JobStore owns only an in-process lock.  ``BEGIN IMMEDIATE``
            # serializes writers across independent connections/process-local
            # store instances before either can inspect terminal truth.
            await session.execute(text("BEGIN IMMEDIATE"))
            row = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == job_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            if expected_claim_owner is not None and (
                row.claim_owner != expected_claim_owner
                or int(row.claim_generation or 0)
                != int(expected_claim_generation or 0)
            ):
                return False
            if row.terminal_event_id or row.status not in {
                JobStatus.PENDING.value,
                JobStatus.RUNNING.value,
            }:
                return False
            events: list[dict[str, Any]] = list(row.events or [])
            next_seq = int(row.last_seq or 0) + 1
            now = datetime.now(UTC).timestamp()
            for event in bundle:
                event["seq"] = next_seq
                event.setdefault("timestamp", now)
                event.setdefault("event_id", uuid.uuid4().hex)
                events.append(event)
                next_seq += 1
            if len(events) > self.MAX_EVENTS_PER_JOB:
                events = events[-self.MAX_EVENTS_PER_JOB :]
            row.events = events
            row.event_count = (row.event_count or 0) + len(bundle)
            row.last_seq = next_seq - 1
            row.status = status.value
            row.finished_at = finished_at
            row.result = result
            row.error = error
            row.terminal_event_id = str(terminal_items[0]["event_id"])
            row.error_log_ref = (
                error_log_ref.model_dump(mode="json")
                if error_log_ref is not None
                else None
            )
            row.claim_owner = None
            row.claim_expires_at = None
            return True

    async def claim_child(
        self,
        job_id: str,
        *,
        owner: str,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> Job | None:
        """Atomically claim eligible child work across runner processes."""
        self._ensure_engine()
        assert self._write_lock is not None
        claimed_at = now or datetime.now(UTC)
        if claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=UTC)
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            row = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == job_id)
                )
            ).scalar_one_or_none()
            if row is None or row.parent_job_id is None:
                return None
            parent = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == row.parent_job_id)
                )
            ).scalar_one_or_none()
            if parent is None or parent.status not in {
                JobStatus.SUCCEEDED.value,
                JobStatus.PARTIAL.value,
            }:
                return None
            if row.status not in {
                JobStatus.PENDING.value,
                JobStatus.RUNNING.value,
            }:
                return None
            expires_at = row.claim_expires_at
            if expires_at is not None and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if row.claim_owner and expires_at is not None and expires_at > claimed_at:
                return None
            row.status = JobStatus.RUNNING.value
            row.claim_owner = owner
            row.claim_generation = int(row.claim_generation or 0) + 1
            row.claim_expires_at = claimed_at + timedelta(seconds=lease_seconds)
            row.started_at = row.started_at or claimed_at
            await session.flush()
            return self._row_to_job(row)

    async def renew_child_claim(
        self,
        job_id: str,
        *,
        owner: str,
        generation: int,
        lease_seconds: float,
    ) -> bool:
        """Extend an active child lease only for its current owner."""
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            row = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == job_id)
                )
            ).scalar_one_or_none()
            if (
                row is None
                or row.parent_job_id is None
                or row.status != JobStatus.RUNNING.value
                or row.claim_owner != owner
                or int(row.claim_generation or 0) != generation
            ):
                return False
            row.claim_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
            return True

    async def claim_is_current(
        self,
        job_id: str,
        *,
        owner: str,
        generation: int,
    ) -> bool:
        """Check the unexpired fencing token immediately before publication."""
        job = await self.get(job_id)
        if (
            job is None
            or job.status != JobStatus.RUNNING
            or job.claim_owner != owner
            or job.claim_generation != generation
            or job.claim_expires_at is None
        ):
            return False
        expires_at = job.claim_expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return expires_at > datetime.now(UTC)

    async def run_if_current_claim(
        self,
        job_id: str,
        *,
        owner: str,
        generation: int,
        operation: Callable[[], Awaitable[Any]],
    ) -> bool:
        """Run an external commit while fencing replacement job claims.

        The operation must not call this JobStore: the jobs write transaction
        stays open until it returns so another process cannot advance the
        claim generation between validation and the external commit.
        """
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            row = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == job_id)
                )
            ).scalar_one_or_none()
            if (
                row is None
                or row.parent_job_id is None
                or row.status != JobStatus.RUNNING.value
                or row.claim_owner != owner
                or int(row.claim_generation or 0) != generation
                or row.claim_expires_at is None
            ):
                return False
            expires_at = row.claim_expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at <= datetime.now(UTC):
                return False
            await operation()
            return True

    async def run_if_child_active(
        self,
        job_id: str,
        *,
        operation: Callable[[], Awaitable[Any]],
    ) -> bool:
        """Run an external commit only while a durable child is active.

        The jobs write transaction stays open across ``operation`` so a child
        cannot terminalize between the active-state check and the external
        resource reset. The operation must not call this JobStore.
        """
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            row = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == job_id)
                )
            ).scalar_one_or_none()
            if (
                row is None
                or row.parent_job_id is None
                or row.status
                not in {JobStatus.PENDING.value, JobStatus.RUNNING.value}
            ):
                return False
            await operation()
            return True

    async def run_if_child_active_or_delete(
        self,
        job_id: str,
        *,
        operation: Callable[[], Awaitable[bool]],
    ) -> bool:
        """Commit a resource bind or atomically remove its unbound child.

        The jobs write transaction remains held while ``operation`` performs
        the resource CAS. A competing store therefore cannot claim the child
        between a false CAS result and deletion. Lock order is jobs then
        resources, matching the existing child claim guards.
        """
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            row = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == job_id)
                )
            ).scalar_one_or_none()
            if (
                row is None
                or row.parent_job_id is None
                or row.status
                not in {JobStatus.PENDING.value, JobStatus.RUNNING.value}
            ):
                return False
            parent = (
                await session.execute(
                    select(JobRow).where(JobRow.job_id == row.parent_job_id)
                )
            ).scalar_one_or_none()
            if parent is None or parent.status not in {
                JobStatus.SUCCEEDED.value,
                JobStatus.PARTIAL.value,
            }:
                return False
            if await operation():
                return True
            await session.delete(row)
            return False

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

    async def get_children(self, parent_job_id: str) -> list[Job]:
        self._ensure_engine()
        async with self._with_session() as session:
            rows = (
                await session.execute(
                    select(JobRow)
                    .where(JobRow.parent_job_id == parent_job_id)
                    .order_by(JobRow.created_at.asc(), JobRow.id.asc())
                )
            ).scalars().all()
            return [self._row_to_job(row) for row in rows]

    async def get_with_children(self, job_id: str) -> dict[str, Any] | None:
        """Return a public job projection with durable child state."""
        job = await self.get(job_id)
        if job is None:
            return None
        children = await self.get_children(job_id)
        return self._project_with_children(job, children, full=True)

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
            stmt = select(JobRow).where(
                JobRow.user_id == user_id,
                JobRow.parent_job_id.is_(None),
            )
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
            jobs = [self._row_to_job(r) for r in rows if r is not None]
        return [
            self._project_with_children(job, await self.get_children(job.job_id))
            for job in jobs
        ]

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
                .where(
                    JobRow.session_id == session_id,
                    JobRow.parent_job_id.is_(None),
                )
                .order_by(JobRow.created_at.desc(), JobRow.id.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).scalars().all()
            jobs = [self._row_to_job(row) for row in reversed(rows)]
        return [
            self._project_with_children(job, await self.get_children(job.job_id))
            for job in jobs
        ]

    async def count(
        self,
        user_id: str,
        *,
        status: JobStatus | None = None,
    ) -> int:
        self._ensure_engine()
        async with self._with_session() as session:
            stmt = select(JobRow).where(
                JobRow.user_id == user_id,
                JobRow.parent_job_id.is_(None),
            )
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
                    select(JobRow).where(
                        JobRow.user_id == user_id,
                        JobRow.parent_job_id.is_(None),
                    )
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
            parent_job_id=job.parent_job_id,
            task_kind=job.task_kind,
            dedupe_key=job.dedupe_key,
            claim_owner=job.claim_owner,
            claim_expires_at=job.claim_expires_at,
            claim_generation=job.claim_generation,
            status=job.status.value,
            message=job.message or "",
            language=job.language,
            metadata_json=dict(job.metadata or {}),
            web_search_enabled=bool(job.web_search_enabled),
            error=job.error,
            error_log_ref=(
                job.error_log_ref.model_dump(mode="json")
                if job.error_log_ref is not None
                else None
            ),
            terminal_event_id=job.terminal_event_id,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
            result=job.result,
            event_count=job.event_count,
            last_seq=job.last_seq,
            events=list(job.events or []),
        )

    @staticmethod
    def _project_with_children(
        job: Job,
        children: list[Job],
        *,
        full: bool = False,
    ) -> dict[str, Any]:
        from tutor.core.redaction import redact_sensitive

        payload = job.to_full_dict() if full else job.to_summary()
        child_payloads: list[dict[str, Any]] = []
        for child in children:
            projected = child.to_summary()
            projected["metadata"] = redact_sensitive(dict(child.metadata or {}))
            child_payloads.append(projected)
        payload["children"] = child_payloads
        payload["background_status"] = _background_status(children)
        public_payload = redact_sensitive(payload)
        return public_payload if isinstance(public_payload, dict) else {}

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
            parent_job_id=row.parent_job_id,
            task_kind=row.task_kind,
            dedupe_key=row.dedupe_key,
            claim_owner=row.claim_owner,
            claim_expires_at=row.claim_expires_at,
            claim_generation=int(row.claim_generation or 0),
            status=status,
            message=row.message or "",
            language=row.language or "zh",
            metadata=dict(row.metadata_json or {}),
            web_search_enabled=bool(row.web_search_enabled),
            error=row.error,
            error_log_ref=(
                ArtifactRef.model_validate(row.error_log_ref)
                if row.error_log_ref
                else None
            ),
            terminal_event_id=row.terminal_event_id,
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


def _background_status(children: list[Job]) -> str | None:
    if not children:
        return None
    statuses = {child.status for child in children}
    if JobStatus.RUNNING in statuses:
        return JobStatus.RUNNING.value
    if JobStatus.PENDING in statuses:
        return JobStatus.PENDING.value
    failed = statuses & {JobStatus.FAILED, JobStatus.CANCELLED}
    succeeded = statuses & {JobStatus.SUCCEEDED, JobStatus.PARTIAL}
    if failed and succeeded:
        return JobStatus.PARTIAL.value
    if failed:
        return JobStatus.FAILED.value
    if JobStatus.PARTIAL in statuses:
        return JobStatus.PARTIAL.value
    return JobStatus.SUCCEEDED.value


__all__ = ["JobStore", "get_job_store", "reset_job_store"]
