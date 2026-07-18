"""Application-owned SQLite store for general exercise responses."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    String,
    UniqueConstraint,
    delete,
    select,
    text,
    update,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import Integer as SqlInteger

from tutor.services.exercise_responses.schema import (
    ExerciseDraft,
    ExerciseResponseState,
    ExerciseSubmission,
)


class ExerciseResponseConflictError(ValueError):
    code = "SUBMISSION_ID_CONFLICT"


@dataclass(frozen=True)
class UnpublishedSubmissionRecord:
    row_id: int
    submission: ExerciseSubmission


class _Base(DeclarativeBase):
    pass


class ExerciseDraftRow(_Base):
    __tablename__ = "exercise_drafts"

    id = Column(
        BigInteger().with_variant(SqlInteger, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    user_id = Column(String(128), nullable=False)
    package_id = Column(String(64), nullable=False)
    resource_id = Column(String(64), nullable=False)
    question_id = Column(String(64), nullable=False)
    question_type = Column(String(32), nullable=False)
    answer_json = Column(JSON, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "package_id",
            "resource_id",
            "question_id",
            name="uq_exercise_draft_owner_question",
        ),
    )


class ExerciseSubmissionRow(_Base):
    __tablename__ = "exercise_submissions"

    id = Column(
        BigInteger().with_variant(SqlInteger, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    submission_id = Column(String(64), nullable=False, unique=True)
    client_submission_id = Column(String(64), nullable=True)
    user_id = Column(String(128), nullable=False, index=True)
    session_id = Column(String(64), nullable=False, default="")
    package_id = Column(String(64), nullable=False, index=True)
    resource_id = Column(String(64), nullable=False, index=True)
    question_id = Column(String(64), nullable=False, index=True)
    question_type = Column(String(32), nullable=False)
    answer_json = Column(JSON, nullable=False)
    correct = Column(Boolean, nullable=False)
    score = Column(Float, nullable=False)
    concept_id = Column(String(128), nullable=False, default="")
    course = Column(String(128), nullable=False, default="")
    linked_code_attempt_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, index=True)
    event_published = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "client_submission_id",
            name="uq_exercise_submission_owner_client_id",
        ),
        Index(
            "ix_exercise_submission_owner_question_time",
            "user_id",
            "package_id",
            "resource_id",
            "question_id",
            "created_at",
        ),
    )


class ExerciseResponseStore:
    """Persist replaceable drafts and immutable terminal submissions."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._engine: AsyncEngine | None = None
        self._sessionmaker: async_sessionmaker[AsyncSession] | None = None
        self._write_lock: asyncio.Lock | None = None
        self._lock = threading.Lock()

    async def init(self) -> None:
        engine = self._ensure_engine()
        async with engine.begin() as connection:
            await connection.run_sync(_Base.metadata.create_all)

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

        class _Context:
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

        return _Context()

    async def upsert_draft(self, draft: ExerciseDraft) -> ExerciseDraft:
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            row = (
                await session.execute(
                    select(ExerciseDraftRow).where(
                        ExerciseDraftRow.user_id == draft.user_id,
                        ExerciseDraftRow.package_id == draft.package_id,
                        ExerciseDraftRow.resource_id == draft.resource_id,
                        ExerciseDraftRow.question_id == draft.question_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                session.add(
                    ExerciseDraftRow(
                        user_id=draft.user_id,
                        package_id=draft.package_id,
                        resource_id=draft.resource_id,
                        question_id=draft.question_id,
                        question_type=draft.question_type.value,
                        answer_json=draft.answer_json,
                        updated_at=draft.updated_at,
                    )
                )
            else:
                row.question_type = draft.question_type.value
                row.answer_json = draft.answer_json
                row.updated_at = draft.updated_at
        return draft

    async def get_state(
        self,
        user_id: str,
        package_id: str,
        resource_id: str,
        question_id: str,
    ) -> ExerciseResponseState:
        self._ensure_engine()
        async with self._with_session() as session:
            draft_row = (
                await session.execute(
                    select(ExerciseDraftRow).where(
                        ExerciseDraftRow.user_id == user_id,
                        ExerciseDraftRow.package_id == package_id,
                        ExerciseDraftRow.resource_id == resource_id,
                        ExerciseDraftRow.question_id == question_id,
                    )
                )
            ).scalar_one_or_none()
            submission_rows = (
                await session.execute(
                    select(ExerciseSubmissionRow)
                    .where(
                        ExerciseSubmissionRow.user_id == user_id,
                        ExerciseSubmissionRow.package_id == package_id,
                        ExerciseSubmissionRow.resource_id == resource_id,
                        ExerciseSubmissionRow.question_id == question_id,
                    )
                    .order_by(
                        ExerciseSubmissionRow.created_at.desc(),
                        ExerciseSubmissionRow.id.desc(),
                    )
                )
            ).scalars().all()
        return ExerciseResponseState(
            draft=self._row_to_draft(draft_row) if draft_row is not None else None,
            submissions=[self._row_to_submission(row) for row in submission_rows],
        )

    async def save_submission(
        self, submission: ExerciseSubmission
    ) -> ExerciseSubmission:
        """Insert once and clear the matching draft in the same transaction."""
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            if submission.client_submission_id:
                existing = (
                    await session.execute(
                        select(ExerciseSubmissionRow).where(
                            ExerciseSubmissionRow.user_id == submission.user_id,
                            ExerciseSubmissionRow.client_submission_id
                            == submission.client_submission_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    persisted = self._row_to_submission(existing)
                    if self._idempotency_key(persisted) != self._idempotency_key(
                        submission
                    ):
                        raise ExerciseResponseConflictError()
                    return persisted

            existing_id = (
                await session.execute(
                    select(ExerciseSubmissionRow).where(
                        ExerciseSubmissionRow.submission_id
                        == submission.submission_id
                    )
                )
            ).scalar_one_or_none()
            if existing_id is not None:
                persisted = self._row_to_submission(existing_id)
                if self._idempotency_key(persisted) != self._idempotency_key(
                    submission
                ):
                    raise ExerciseResponseConflictError()
                return persisted

            session.add(
                ExerciseSubmissionRow(
                    submission_id=submission.submission_id,
                    client_submission_id=submission.client_submission_id,
                    user_id=submission.user_id,
                    session_id=submission.session_id,
                    package_id=submission.package_id,
                    resource_id=submission.resource_id,
                    question_id=submission.question_id,
                    question_type=submission.question_type.value,
                    answer_json=submission.answer_json,
                    correct=submission.correct,
                    score=submission.score,
                    concept_id=submission.concept_id,
                    course=submission.course,
                    linked_code_attempt_id=submission.linked_code_attempt_id,
                    created_at=submission.created_at,
                    event_published=submission.event_published,
                )
            )
            await session.execute(
                delete(ExerciseDraftRow).where(
                    ExerciseDraftRow.user_id == submission.user_id,
                    ExerciseDraftRow.package_id == submission.package_id,
                    ExerciseDraftRow.resource_id == submission.resource_id,
                    ExerciseDraftRow.question_id == submission.question_id,
                )
            )
        return submission

    async def get_submission_for_user(
        self, submission_id: str, user_id: str
    ) -> ExerciseSubmission | None:
        self._ensure_engine()
        async with self._with_session() as session:
            row = (
                await session.execute(
                    select(ExerciseSubmissionRow).where(
                        ExerciseSubmissionRow.submission_id == submission_id,
                        ExerciseSubmissionRow.user_id == user_id,
                    )
                )
            ).scalar_one_or_none()
        return self._row_to_submission(row) if row is not None else None

    async def list_unpublished_page(
        self, *, after_row_id: int, limit: int = 1000
    ) -> list[UnpublishedSubmissionRecord]:
        self._ensure_engine()
        async with self._with_session() as session:
            rows = (
                await session.execute(
                    select(ExerciseSubmissionRow)
                    .where(
                        ExerciseSubmissionRow.event_published.is_(False),
                        ExerciseSubmissionRow.id > max(0, after_row_id),
                    )
                    .order_by(ExerciseSubmissionRow.id)
                    .limit(limit)
                )
            ).scalars().all()
        return [
            UnpublishedSubmissionRecord(
                row_id=int(row.id), submission=self._row_to_submission(row)
            )
            for row in rows
        ]

    async def mark_event_published(self, submission_id: str, user_id: str) -> bool:
        self._ensure_engine()
        assert self._write_lock is not None
        async with self._write_lock, self._with_session() as session:
            result = await session.execute(
                update(ExerciseSubmissionRow)
                .where(
                    ExerciseSubmissionRow.submission_id == submission_id,
                    ExerciseSubmissionRow.user_id == user_id,
                )
                .values(event_published=True)
            )
            return bool(result.rowcount)

    @staticmethod
    def _idempotency_key(submission: ExerciseSubmission) -> tuple[object, ...]:
        return (
            submission.user_id,
            submission.session_id,
            submission.package_id,
            submission.resource_id,
            submission.question_id,
            submission.question_type.value,
            submission.answer_json,
            submission.correct,
            submission.score,
            submission.concept_id,
            submission.course,
            submission.linked_code_attempt_id,
        )

    @staticmethod
    def _row_to_draft(row: ExerciseDraftRow) -> ExerciseDraft:
        return ExerciseDraft.model_validate(
            {
                "user_id": row.user_id,
                "package_id": row.package_id,
                "resource_id": row.resource_id,
                "question_id": row.question_id,
                "question_type": row.question_type,
                "answer_json": row.answer_json,
                "updated_at": row.updated_at,
            }
        )

    @staticmethod
    def _row_to_submission(row: ExerciseSubmissionRow) -> ExerciseSubmission:
        return ExerciseSubmission.model_validate(
            {
                "submission_id": row.submission_id,
                "client_submission_id": row.client_submission_id,
                "user_id": row.user_id,
                "session_id": row.session_id or "",
                "package_id": row.package_id,
                "resource_id": row.resource_id,
                "question_id": row.question_id,
                "question_type": row.question_type,
                "answer_json": row.answer_json,
                "correct": bool(row.correct),
                "score": row.score,
                "concept_id": row.concept_id or "",
                "course": row.course or "",
                "linked_code_attempt_id": row.linked_code_attempt_id,
                "created_at": row.created_at,
                "event_published": bool(row.event_published),
            }
        )


__all__ = [
    "ExerciseResponseConflictError",
    "ExerciseResponseStore",
    "UnpublishedSubmissionRecord",
]
