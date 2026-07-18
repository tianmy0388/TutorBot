"""Public, answer-safe contracts for general exercise responses."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExerciseQuestionType(StrEnum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    TRUE_FALSE = "true_false"
    FILL_BLANK = "fill_blank"
    SHORT_ANSWER = "short_answer"
    CODE = "code"


class ExerciseGradingStatus(StrEnum):
    AUTO_GRADED = "auto_graded"
    MANUAL_REVIEW = "manual_review"


def _normalized_request_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("answer must be text")
    return " ".join(value.split()).casefold()


def _normalized_request_boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _normalized_request_text(value)
    if normalized in {"true", "t", "yes", "y", "1", "对", "正确", "是"}:
        return True
    if normalized in {"false", "f", "no", "n", "0", "错", "错误", "否"}:
        return False
    raise ValueError("answer must be boolean")


def exercise_submission_request_identity(
    *,
    session_id: str,
    package_id: str,
    resource_id: str,
    question_id: str,
    question_type: ExerciseQuestionType | str,
    answer_json: Any,
    linked_code_attempt_id: str | None,
) -> tuple[object, ...]:
    """Return the stable client-controlled identity for idempotent retries."""
    kind = ExerciseQuestionType(question_type)
    if kind == ExerciseQuestionType.CODE:
        normalized_answer: object = None
    elif kind == ExerciseQuestionType.MULTIPLE_CHOICE:
        if not isinstance(answer_json, list) or not answer_json:
            raise ValueError("multiple-choice answer must be a non-empty list")
        normalized_items = [_normalized_request_text(item) for item in answer_json]
        if len(set(normalized_items)) != len(normalized_items):
            raise ValueError("multiple-choice answer contains duplicates")
        normalized_answer = tuple(sorted(normalized_items))
    elif kind == ExerciseQuestionType.TRUE_FALSE:
        normalized_answer = _normalized_request_boolean(answer_json)
    else:
        normalized_answer = _normalized_request_text(answer_json)
    return (
        session_id,
        package_id,
        resource_id,
        question_id,
        normalized_answer,
        linked_code_attempt_id,
    )


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
    grading_status: ExerciseGradingStatus = ExerciseGradingStatus.AUTO_GRADED
    correct: bool | None
    score: float | None = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    concept_id: str = Field(default="", max_length=128)
    course: str = Field(default="", max_length=128)
    linked_code_attempt_id: str | None = Field(
        default=None, min_length=1, max_length=64
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_published: bool = Field(default=False, exclude=True)

    @model_validator(mode="after")
    def _grading_fields_match_status(self) -> ExerciseSubmission:
        if self.grading_status == ExerciseGradingStatus.AUTO_GRADED:
            if self.correct is None or self.score is None:
                raise ValueError("auto-graded submissions require correct and score")
        elif self.correct is not None or self.score is not None:
            raise ValueError("manual-review submissions cannot carry a score")
        return self

    def client_request_identity(self) -> tuple[object, ...]:
        return exercise_submission_request_identity(
            session_id=self.session_id,
            package_id=self.package_id,
            resource_id=self.resource_id,
            question_id=self.question_id,
            question_type=self.question_type,
            answer_json=self.answer_json,
            linked_code_attempt_id=self.linked_code_attempt_id,
        )


class ExerciseResponseState(BaseModel):
    """Owner-scoped draft plus terminal history for one question."""

    model_config = ConfigDict(extra="forbid")

    draft: ExerciseDraft | None = None
    submissions: list[ExerciseSubmission] = Field(default_factory=list)


__all__ = [
    "ExerciseDraft",
    "ExerciseGradingStatus",
    "ExerciseQuestionType",
    "ExerciseResponseState",
    "ExerciseSubmission",
    "exercise_submission_request_identity",
]
