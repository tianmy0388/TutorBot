"""Public, redacted contracts for Python exercise submissions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

SUBMISSION_POLICY_TIMEOUT_SECONDS = 3
SUBMISSION_POLICY_CHECK_COUNT = 2
SUBMISSION_SCHEDULING_MARGIN_SECONDS = 5
MAX_CODE_EXECUTION_SECONDS = 10


def submission_pipeline_budget_seconds(code_timeout_seconds: int) -> int:
    """Budget both policy subprocesses, execution and scheduling overhead."""
    return (
        SUBMISSION_POLICY_CHECK_COUNT * SUBMISSION_POLICY_TIMEOUT_SECONDS
        + max(1, code_timeout_seconds)
        + SUBMISSION_SCHEDULING_MARGIN_SECONDS
    )


MAX_SUBMISSION_PIPELINE_SECONDS = submission_pipeline_budget_seconds(
    MAX_CODE_EXECUTION_SECONDS
)
DEFAULT_ATTEMPT_CLAIM_LEASE_SECONDS = MAX_SUBMISSION_PIPELINE_SECONDS + 5


class AttemptStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SYNTAX_ERROR = "syntax_error"
    TIMEOUT = "timeout"
    POLICY_REJECTED = "policy_rejected"
    ERROR = "error"


class TestCaseResult(BaseModel):
    """One safe test projection; expected values and test calls are omitted."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    actual_json: Any | None = None
    error_code: str | None = None


class SubmissionExecutionResult(BaseModel):
    """Terminal output from the dedicated local submission runner."""

    model_config = ConfigDict(extra="forbid")

    status: AttemptStatus
    passed_tests: int = Field(ge=0)
    total_tests: int = Field(ge=0)
    test_results: list[TestCaseResult] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    error_code: str | None = None


class ExerciseAttempt(BaseModel):
    """A durable terminal code-exercise attempt."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    attempt_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=1, max_length=64)
    client_attempt_id: str | None = Field(default=None, min_length=1, max_length=64)
    user_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(default="", max_length=64)
    package_id: str = Field(min_length=1, max_length=64)
    question_id: str = Field(min_length=1, max_length=64)
    concept_id: str = Field(default="", max_length=128)
    course: str = Field(default="", max_length=128)
    source_code: str
    status: AttemptStatus
    passed_tests: int = Field(ge=0)
    total_tests: int = Field(ge=0)
    test_results: list[TestCaseResult] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = Field(default=0.0, ge=0.0, allow_inf_nan=False)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    error_code: str | None = None
    event_published: bool = Field(default=False, exclude=True)

    @model_validator(mode="after")
    def _passed_not_above_total(self) -> ExerciseAttempt:
        if self.passed_tests > self.total_tests:
            raise ValueError("passed_tests cannot exceed total_tests")
        return self


__all__ = [
    "AttemptStatus",
    "DEFAULT_ATTEMPT_CLAIM_LEASE_SECONDS",
    "ExerciseAttempt",
    "MAX_SUBMISSION_PIPELINE_SECONDS",
    "SUBMISSION_POLICY_TIMEOUT_SECONDS",
    "SubmissionExecutionResult",
    "TestCaseResult",
    "submission_pipeline_budget_seconds",
]
