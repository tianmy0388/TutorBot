"""Resource / ResourcePackage schema (Pydantic v2).

A :class:`Resource` is one chunk of learning material — a document,
a mind map, a quiz, a video, etc. Each has:

- Stable ``resource_id`` (uuid4 hex)
- ``type`` — discriminator (one of :class:`ResourceType`)
- ``title`` + Markdown ``content``
- ``format_specific`` — type-dependent payload (validated by per-type model)
- ``difficulty`` (1-5), ``estimated_minutes``
- ``prerequisites`` — concept ids this resource assumes
- ``generated_by`` — list of agent names that contributed
- ``confidence_score`` (0-1) — quality signal

A :class:`ResourcePackage` bundles multiple Resources for one learner +
topic, plus the optional learning path and the snapshot of the profile
that produced it.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from tutor.services.artifacts import UnsafeArtifactKey, resolve_artifact_key

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ResourceType(str, Enum):  # noqa: UP042 - persisted enum compatibility
    """All supported resource types (≥6 per idea.md)."""

    DOCUMENT = "document"      # 课程讲解文档 (Markdown)
    MINDMAP = "mindmap"        # 知识点思维导图 (Mermaid DSL)
    EXERCISE = "exercise"      # 练习题/题库 (JSON)
    READING = "reading"        # 拓展阅读材料 (Markdown + citations)
    VIDEO = "video"            # 多模态视频/动画 (MP4 + Manim source)
    CODE = "code"              # 代码实操案例 (Python + explanation)
    PPT = "ppt"                # PPT 教案 (optional, Phase 5)


class ReviewVerdict(str, Enum):  # noqa: UP042 - persisted enum compatibility
    """Outcome of a quality review."""

    PASS = "pass"
    REVISE = "revise"
    REJECT = "reject"


class ArtifactRef(BaseModel):
    """Portable artifact manifest entry.

    ``path`` is accepted only as a backward-compatible input alias. Relative
    legacy values are promoted to ``artifact_key`` and are never emitted.
    Absolute legacy values remain readable by the resource endpoint, which has
    the data-directory context required to validate them safely.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    artifact_key: str | None = None
    kind: str = ""
    path: str | None = Field(default=None, exclude=True)

    @model_validator(mode="after")
    def _promote_legacy_relative_path(self) -> ArtifactRef:
        if self.artifact_key is None and self.path:
            try:
                candidate = resolve_artifact_key(self.path, Path("."))
            except UnsafeArtifactKey:
                return self
            self.artifact_key = candidate.relative_to(Path(".").resolve()).as_posix()
        if self.artifact_key is not None:
            resolve_artifact_key(self.artifact_key, Path("."))
        return self


# ---------------------------------------------------------------------------
# Format-specific payloads
# ---------------------------------------------------------------------------


class DocumentResource(BaseModel):
    """Payload for ``type=document``."""

    model_config = ConfigDict(extra="forbid")

    sections: list[dict[str, Any]] = Field(default_factory=list)
    # Each section: {"title": "...", "content": "...", "key_points": [...]}
    has_math: bool = False
    has_diagrams: bool = False


class MindMapResource(BaseModel):
    """Payload for ``type=mindmap``. Uses Mermaid ``mindmap`` syntax."""

    model_config = ConfigDict(extra="forbid")

    mermaid_dsl: str = ""  # raw ```mermaid ...``` block (without fences)
    central_topic: str = ""
    branch_count: int = 0
    outline: list[MindMapOutlineItem] = Field(default_factory=list)


class MindMapOutlineItem(BaseModel):
    """One accessible, indentation-derived item in a Mermaid mind map."""

    model_config = ConfigDict(extra="forbid")

    depth: int = Field(ge=0)
    label: str


class ExerciseOption(BaseModel):
    """One option in a multiple-choice question."""

    model_config = ConfigDict(extra="forbid")

    label: str  # "A" / "B" / ...
    text: str


class CodeTestCase(BaseModel):
    """One server-owned test for a generated Python code question."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    call: str = Field(min_length=1, max_length=2000)
    expected_json: Any

    @field_validator("expected_json")
    @classmethod
    def _expected_must_be_standard_json(cls, value: Any) -> Any:
        _validate_standard_json_value(value)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            encoded.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("expected_json strings must be valid UTF-8") from exc
        decoded = json.loads(encoded)
        if json.dumps(
            decoded,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ) != encoded:
            raise ValueError("expected_json must round-trip deterministically")
        return value


def _validate_standard_json_value(value: Any) -> None:
    """Reject Python-only values before tests reach the subprocess wrapper."""
    if value is None or type(value) in {bool, int, str}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("expected_json numbers must be finite")
        return
    if type(value) is list:
        for item in value:
            _validate_standard_json_value(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError("expected_json object keys must be strings")
            _validate_standard_json_value(item)
        return
    raise ValueError("expected_json must contain only standard JSON values")


class CodeSpec(BaseModel):
    """Executable contract persisted with a Python code question."""

    model_config = ConfigDict(extra="forbid")

    language: Literal["python"] = "python"
    starter_code: str = Field(max_length=131072)
    tests: list[CodeTestCase] = Field(min_length=1, max_length=50)
    time_limit_seconds: int = Field(default=5, ge=1, le=10)


class ExerciseQuestion(BaseModel):
    """One question in a quiz/exercise set."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: Literal["single_choice", "multiple_choice", "true_false", "fill_blank", "short_answer", "code"]
    difficulty: int = 2
    knowledge_point: str = ""
    question: str
    options: list[ExerciseOption] = Field(default_factory=list)
    answer: Any = None  # string, list[str], bool, or code string
    accepted_answers: list[str] = Field(default_factory=list)
    explanation: str = ""
    estimated_seconds: int = 60
    # Optional keeps legacy packages readable. Submission validates the
    # executable contract and returns CODE_SPEC_UNAVAILABLE when absent.
    code_spec: CodeSpec | None = None

    @field_validator("difficulty")
    @classmethod
    def _diff_in_range(cls, v: int) -> int:
        if v < 1 or v > 5:
            raise ValueError(f"difficulty must be in [1, 5], got {v}")
        return v


class ExerciseResource(BaseModel):
    """Payload for ``type=exercise``."""

    model_config = ConfigDict(extra="forbid")

    questions: list[ExerciseQuestion] = Field(default_factory=list)
    total_questions: int = 0
    difficulty_breakdown: dict[str, int] = Field(default_factory=dict)
    # e.g. {"basic": 3, "advanced": 2, "challenge": 1}


class ReadingResource(BaseModel):
    """Payload for ``type=reading``. Markdown body + citations."""

    model_config = ConfigDict(extra="forbid")

    citations: list[dict[str, Any]] = Field(default_factory=list)
    # Each: {"title": "...", "url": "...", "author": "...", "year": 2024}
    estimated_reading_minutes: int = 5


class VideoResource(BaseModel):
    """Payload for ``type=video``. Holds Manim source + (optional) render result.

    2026-06-21 plan (C2): ``mp4_path`` is the local filesystem path
    to the rendered MP4, persisted alongside the resource so the
    download endpoint can serve it. ``video_url`` is the HTTP-
    accessible URL (pointing at the same file via static serve).
    """

    model_config = ConfigDict(extra="forbid")

    manim_code: str = ""
    scene_class: str = "GeneratedScene"
    video_url: str | None = None
    artifact_key: str | None = None
    # Local filesystem path (2026-06-21 plan, for archival / download).
    mp4_path: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: int = 0
    render_status: Literal["pending", "rendering", "ready", "failed"] = "pending"
    render_job_id: str | None = None
    render_error: str | None = None
    render_error_code: str | None = None
    render_failure: dict[str, Any] | None = None
    source_revision: int = Field(default=0, ge=0)
    repair_status: Literal["pending", "running", "ready", "failed"] | None = None
    repair_job_id: str | None = None
    repair_history: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class CodeResource(BaseModel):
    """Payload for ``type=code``. Code + explanation + execution result.

    2026-06-21 plan adds structured diagnostics for the run:
      * ``error_code``         — distinguishes runtime-dep-missing
                                 from code-execution-failed
      * ``execution_python``   — which interpreter actually ran
      * ``dependency_versions``— matplotlib / numpy / python version
                                 snapshot for the right-pane footer
      * ``duration_seconds``   — wall-clock time of the run
      * ``artifacts``          — image / SVG files written by the
                                 user code, collected into the
                                 resource so the viewer can render
                                 them inline
    """

    model_config = ConfigDict(extra="forbid")

    language: str = "python"
    code: str = ""
    explanation: str = ""
    output_kind: Literal["text", "figure"] = "text"
    execution_status: Literal["not_run", "pending", "success", "failed", "timeout"] = "not_run"
    stdout: str = ""
    stderr: str = ""
    sandbox_url: str | None = None
    error_code: str | None = None
    execution_python: str = ""
    dependency_versions: dict[str, str] = Field(default_factory=dict)
    duration_seconds: float = 0.0
    artifacts: list[ArtifactRef] = Field(default_factory=list)


class PPTResource(BaseModel):
    """Payload for ``type=ppt``. python-pptx generated deck info."""

    model_config = ConfigDict(extra="forbid")

    slide_count: int = 0
    pptx_path: str | None = None
    artifact_key: str | None = None
    slide_titles: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Resource (root)
# ---------------------------------------------------------------------------


class Resource(BaseModel):
    """One learning resource of any supported type.

    The ``format_specific`` field is validated against the corresponding
    per-type model at construction time. The union type isn't directly
    expressible in Pydantic v2 discriminator syntax, so we use a validator
    that switches on ``type``.
    """

    model_config = ConfigDict(extra="forbid")

    resource_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: ResourceType
    title: str
    content: str = ""  # Markdown body (used directly for document/reading)
    format_specific: dict[str, Any] = Field(default_factory=dict)
    difficulty: int = 2
    estimated_minutes: int = 5
    prerequisites: list[str] = Field(default_factory=list)
    generated_by: list[str] = Field(default_factory=list)
    confidence_score: float = 0.7
    topic: str = ""
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------------
    # Evidence fields (Task 11)
    # ------------------------------------------------------------------
    # These are the four evidence surfaces the UI shows next to every
    # resource: where the facts came from, how good the resource is,
    # whether the safety agent flagged anything, and which agents ran.

    citations: list[dict[str, Any]] = Field(default_factory=list)
    review: dict[str, Any] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
    unverified_claims: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("title")
    @classmethod
    def _title_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("title must be non-empty")
        return v.strip()

    @field_validator("difficulty")
    @classmethod
    def _difficulty_in_range(cls, v: int) -> int:
        if v < 1 or v > 5:
            raise ValueError(f"difficulty must be in [1, 5], got {v}")
        return v

    @field_validator("estimated_minutes")
    @classmethod
    def _minutes_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"estimated_minutes must be >= 0, got {v}")
        return v

    @field_validator("confidence_score")
    @classmethod
    def _confidence_in_range(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"confidence_score must be finite, got {v!r}")
        return max(0.0, min(1.0, float(v)))

    @model_validator(mode="after")
    def _validate_format_specific(self) -> Resource:
        expected_type = self.type
        expected_key = _format_specific_key(expected_type)
        if not self.format_specific:
            return self
        if expected_key not in self.format_specific and len(self.format_specific) > 0:
            # Allow other keys but warn via metadata — strict mode would reject
            self.metadata.setdefault("_format_specific_keys", list(self.format_specific.keys()))
        return self

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def parsed_format_specific(self) -> Any:
        """Return ``format_specific`` validated against the per-type model.

        The ``format_specific`` dict is directly validated as the type's
        payload (its keys match the per-type model's fields). If validation
        fails (e.g. extra keys), returns ``None``.
        """
        if not self.format_specific:
            return None
        model = _FORMAT_MODELS[self.type]
        try:
            return model.model_validate(self.format_specific)
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Resource review (QualityReviewer output)
# ---------------------------------------------------------------------------


class ResourceReview(BaseModel):
    """Outcome of a :class:`QualityReviewer` pass over a Resource."""

    model_config = ConfigDict(extra="forbid")

    resource_id: str
    verdict: ReviewVerdict = ReviewVerdict.PASS
    quality_score: float = 0.8  # 0-1
    issues: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    reviewer: str = "QualityReviewerAgent"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("quality_score")
    @classmethod
    def _score_in_range(cls, v: float) -> float:
        if math.isnan(v) or math.isinf(v):
            raise ValueError(f"quality_score must be finite, got {v!r}")
        return max(0.0, min(1.0, float(v)))


# ---------------------------------------------------------------------------
# Package
# ---------------------------------------------------------------------------


class ResourcePackage(BaseModel):
    """A bundle of resources generated for one learner + one topic."""

    model_config = ConfigDict(extra="forbid")

    package_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    topic: str
    resources: list[Resource] = Field(default_factory=list)
    target_profile_snapshot: dict[str, Any] = Field(default_factory=dict)
    # ^ snapshot of LearnerProfile.to_summary() at generation time
    learning_path_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    generated_by: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def originating_job_id(self) -> str | None:
        """Return the generation job explicitly associated with this package."""
        value = self.metadata.get("job_id")
        return str(value) if value else None

    def associate_originating_job(self, job_id: str | None) -> None:
        """Persist a typed association using the compatible metadata field."""
        if job_id:
            self.metadata["job_id"] = str(job_id)

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def by_type(self, rtype: ResourceType) -> list[Resource]:
        return [r for r in self.resources if r.type == rtype]

    def has_type(self, rtype: ResourceType) -> bool:
        return any(r.type == rtype for r in self.resources)

    def total_minutes(self) -> int:
        return sum(r.estimated_minutes for r in self.resources)

    def summary(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "topic": self.topic,
            "resource_count": len(self.resources),
            "total_minutes": self.total_minutes(),
            "types": sorted({r.type.value for r in self.resources}),
            "avg_confidence": (
                round(sum(r.confidence_score for r in self.resources) / len(self.resources), 3)
                if self.resources
                else 0.0
            ),
            "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_FORMAT_MODELS: dict[ResourceType, type[BaseModel]] = {
    ResourceType.DOCUMENT: DocumentResource,
    ResourceType.MINDMAP: MindMapResource,
    ResourceType.EXERCISE: ExerciseResource,
    ResourceType.READING: ReadingResource,
    ResourceType.VIDEO: VideoResource,
    ResourceType.CODE: CodeResource,
    ResourceType.PPT: PPTResource,
}


def _format_specific_key(rtype: ResourceType) -> str:
    """Map ``ResourceType`` to the conventional key inside ``format_specific``.

    e.g. ``ResourceType.DOCUMENT → "document"`` — but most keys are just
    the type value. We define this explicitly to allow future divergence.
    """
    return rtype.value


def build_resource(
    *,
    type: ResourceType,
    title: str,
    content: str = "",
    format_specific: dict[str, Any] | None = None,
    difficulty: int = 2,
    estimated_minutes: int = 5,
    prerequisites: list[str] | None = None,
    generated_by: list[str] | None = None,
    confidence_score: float = 0.7,
    topic: str = "",
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Resource:
    """Convenience builder with sensible defaults."""
    return Resource(
        type=type,
        title=title,
        content=content,
        format_specific=format_specific or {},
        difficulty=difficulty,
        estimated_minutes=estimated_minutes,
        prerequisites=list(prerequisites or []),
        generated_by=list(generated_by or []),
        confidence_score=confidence_score,
        topic=topic,
        tags=list(tags or []),
        metadata=dict(metadata or {}),
    )


def public_resource_dump(resource: Resource) -> dict[str, Any]:
    """Return the browser-safe projection of one resource.

    Persisted code specs remain complete for server-side judging. Browser
    projections intentionally omit reference answers, test expressions and
    expected values while retaining enough metadata to render the editor.
    """
    data = resource.model_dump(mode="json")
    if resource.type == ResourceType.VIDEO:
        from tutor.services.manim_render.executor import (
            safe_failure_summary,
            sanitize_public_diagnostic,
            tail_lines,
        )

        format_specific = dict(data.get("format_specific") or {})
        format_specific.pop("repair_candidate_code", None)
        format_specific.pop("repair_candidate_failure", None)
        format_specific["repair_history"] = _public_repair_history(
            format_specific.get("repair_history")
        )
        failure = format_specific.get("render_failure")
        if isinstance(failure, dict):
            fallback = "渲染流程未生成可播放视频。"
            summary = safe_failure_summary(
                str(failure.get("summary") or fallback),
                fallback=fallback,
            )
            raw_tail = failure.get("traceback_tail")
            diagnostic_text = "\n".join(
                str(line) for line in raw_tail
            ) if isinstance(raw_tail, list) else str(raw_tail or "")
            error_code = sanitize_public_diagnostic(
                str(failure.get("error_code") or "internal_error")
            )[:120]
            public_failure: dict[str, Any] = {
                "error_code": error_code,
                "summary": summary,
                "traceback_tail": list(tail_lines(diagnostic_text)),
            }
            log_key = _safe_manim_log_artifact_key(
                failure.get("log_artifact_key")
            )
            if log_key:
                public_failure["log_artifact_key"] = log_key
            format_specific["render_failure"] = public_failure
            format_specific["render_error_code"] = error_code
            format_specific["render_error"] = summary
        elif format_specific.get("render_status") == "failed":
            # Pre-structured Manim records stored the complete host traceback
            # in render_error. Never expose that legacy blob to a browser.
            format_specific["render_error_code"] = sanitize_public_diagnostic(
                str(
                    format_specific.get("render_error_code")
                    or "legacy_render_failure"
                )
            )[:120]
            format_specific["render_error"] = "渲染流程未生成可播放视频。"
        data["format_specific"] = format_specific
        return data
    if resource.type != ResourceType.EXERCISE:
        return data
    format_specific = dict(data.get("format_specific") or {})
    questions = format_specific.get("questions")
    if not isinstance(questions, list):
        data["content"] = ""
        return data
    public_questions: list[Any] = []
    for raw in questions:
        if not isinstance(raw, dict):
            public_questions.append(raw)
            continue
        question = dict(raw)
        question.pop("answer", None)
        question.pop("accepted_answers", None)
        question.pop("explanation", None)
        if raw.get("type") != "code":
            public_questions.append(question)
            continue
        spec = question.get("code_spec")
        if isinstance(spec, dict):
            tests = spec.get("tests")
            question["code_spec"] = {
                "language": spec.get("language", "python"),
                "starter_code": spec.get("starter_code", ""),
                "time_limit_seconds": spec.get("time_limit_seconds", 5),
                "test_count": len(tests) if isinstance(tests, list) else 0,
            }
        public_questions.append(question)
    format_specific["questions"] = public_questions
    data["format_specific"] = format_specific
    # Generated exercise Markdown historically embeds answers and explanations.
    # The structured question/options projection is the only browser surface.
    data["content"] = ""
    return data


def _public_repair_history(value: Any) -> list[dict[str, Any]]:
    """Project persisted repair attempts to a bounded browser-safe shape."""
    from tutor.services.manim_render.executor import sanitize_public_diagnostic

    if not isinstance(value, list):
        return []
    projected: list[dict[str, Any]] = []
    for raw in value[-10:]:
        if not isinstance(raw, dict):
            continue
        try:
            failed_revision = max(0, int(raw.get("failed_revision") or 0))
        except (TypeError, ValueError):
            failed_revision = 0
        record: dict[str, Any] = {
            "job_id": sanitize_public_diagnostic(
                str(raw.get("job_id") or "")
            )[:96],
            "failed_revision": failed_revision,
            "status": sanitize_public_diagnostic(
                str(raw.get("status") or "failed")
            )[:20],
        }
        if raw.get("error_code"):
            record["error_code"] = sanitize_public_diagnostic(
                str(raw["error_code"])
            )[:120]
        if raw.get("summary"):
            record["summary"] = sanitize_public_diagnostic(
                str(raw["summary"])
            )[:200]
        log_key = _safe_manim_log_artifact_key(raw.get("log_artifact_key"))
        if log_key:
            record["log_artifact_key"] = log_key
        projected.append(record)
    return projected


def _safe_manim_log_artifact_key(value: Any) -> str:
    key = str(value or "")
    if not key.startswith("manim_logs/"):
        return ""
    try:
        resolve_artifact_key(key, Path("."))
    except UnsafeArtifactKey:
        return ""
    return key


def public_package_dump(package: ResourcePackage) -> dict[str, Any]:
    """Return a package with every child passed through public projection."""
    data = package.model_dump(mode="json", exclude={"resources"})
    data["resources"] = [public_resource_dump(resource) for resource in package.resources]
    return data


__all__ = [
    "ArtifactRef",
    "CodeResource",
    "CodeSpec",
    "CodeTestCase",
    "DocumentResource",
    "ExerciseOption",
    "ExerciseQuestion",
    "ExerciseResource",
    "MindMapOutlineItem",
    "MindMapResource",
    "PPTResource",
    "ReadingResource",
    "Resource",
    "ResourcePackage",
    "ResourceReview",
    "ResourceType",
    "ReviewVerdict",
    "VideoResource",
    "build_resource",
    "public_package_dump",
    "public_resource_dump",
]
