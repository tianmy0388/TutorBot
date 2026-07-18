"""Public, answer-safe contracts for general exercise responses."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExerciseQuestionType(StrEnum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    FILL_BLANK = "fill_blank"
    SHORT_ANSWER = "short_answer"
    CODE = "code"


class ExerciseDraft(BaseModel):
    """One owner-scoped, replaceable in-progress answer."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    user_id: str = Field(min_length=1, max_length=128)
    package_id: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=64)
    question_id: str = Field(min_length=1, max_length=64)
    question_type: ExerciseQuestionType
    answer_json: Any
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ExerciseSubmission(BaseModel):
    """A durable terminal answer scored from server-owned reference data."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    submission_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=64
    )
    client_submission_id: str | None = Field(
        default=None, min_length=1, max_length=64
    )
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(default="", max_length=64)
    package_id: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=64)
    question_id: str = Field(min_length=1, max_length=64)
    question_type: ExerciseQuestionType
    answer_json: Any
    correct: bool
    score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    concept_id: str = Field(default="", max_length=128)
    course: str = Field(default="", max_length=128)
    linked_code_attempt_id: str | None = Field(
        default=None, min_length=1, max_length=64
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_published: bool = Field(default=False, exclude=True)


class ExerciseResponseState(BaseModel):
    """Owner-scoped draft plus terminal history for one question."""

    model_config = ConfigDict(extra="forbid")

    draft: ExerciseDraft | None = None
    submissions: list[ExerciseSubmission] = Field(default_factory=list)


__all__ = [
    "ExerciseDraft",
    "ExerciseQuestionType",
    "ExerciseResponseState",
    "ExerciseSubmission",
]
