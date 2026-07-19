"""ProfileBuilder — high-level orchestration on top of :class:`ProfileStore`.

This module encodes the business logic of *how* to operate on a profile.
The store is the persistence layer; the builder is the policy layer.

Responsibilities
----------------
- Build a fresh profile from a user's self-description (one-shot).
- Ingest new evidence (test results, exercise outcomes, dialogue signals)
  and produce a :class:`ProfileDiff`.
- Merge multiple diffs deterministically.
- Compute aggregates (avg mastery, weakest concept, modality recommendation).
- Suggest next learning steps (in conjunction with a knowledge graph).

The builder is *stateless*: it reads/writes through :class:`ProfileStore`.
"""

from __future__ import annotations

import math
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from tutor.services.learner_profile.schema import (
    CognitiveStyle,
    ErrorPattern,
    GoalType,
    LearnerProfile,
    ModalityPreferences,
    MotivationProfile,
    PaceProfile,
    ProfileDiff,
    Urgency,
    empty_profile,
)
from tutor.services.learner_profile.store import (
    ProfileStore,
    get_profile_store,
)
from tutor.services.learning_events.schema import EventType, LearningEvent

# ---------------------------------------------------------------------------
# Evidence types
# ---------------------------------------------------------------------------


@dataclass
class ExerciseResult:
    """The outcome of one practice question."""

    concept: str
    correct: bool
    difficulty: int = 3  # 1-5
    elapsed_seconds: int = 60
    mistake_type: str | None = None
    note: str = ""

    def to_diff(self) -> ProfileDiff:
        """Translate this result into a :class:`ProfileDiff`.

        Rules of thumb:
        - Correct answer: +0.1 mastery (scaled by difficulty, capped at 0.25)
        - Incorrect: -0.05 (less aggressive) + record error pattern
        """
        delta = 0.0
        if self.correct:
            delta = min(0.25, 0.05 + 0.05 * self.difficulty)
        else:
            delta = -0.05

        diff = ProfileDiff(knowledge_delta={self.concept: delta})
        if not self.correct and self.mistake_type:
            diff.error_pattern = ErrorPattern(
                concept=self.concept,
                mistake_type=self.mistake_type,
                frequency=1,
                examples=[self.note] if self.note else [],
            )
        return diff


@dataclass
class DialogueSignal:
    """A natural-language utterance from the student.

    This is what the LLM-based FeatureExtractorAgent would emit.
    """

    raw_text: str
    extracted_features: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5

    def to_diff(self) -> ProfileDiff:
        """Translate extracted features into a :class:`ProfileDiff`."""
        diff = ProfileDiff()

        # Knowledge observations
        knowledge = self.extracted_features.get("knowledge") or {}
        for concept, level in knowledge.items():
            try:
                level_f = float(level)
            except (TypeError, ValueError):
                continue
            # "knows"/"familiar" → high mastery; "unfamiliar" → low
            level_f = max(0.0, min(1.0, level_f))
            diff.knowledge_set[concept] = level_f

        # Cognitive style
        cs = self.extracted_features.get("cognitive_style")
        if isinstance(cs, str):
            try:
                diff.cognitive_style = CognitiveStyle(cs)
            except ValueError:
                pass

        # Motivation
        mot = self.extracted_features.get("motivation") or {}
        motivation = MotivationProfile()
        if "goal_type" in mot:
            try:
                motivation.goal_type = GoalType(mot["goal_type"])
            except ValueError:
                pass
        if "urgency" in mot:
            try:
                motivation.urgency = Urgency(mot["urgency"])
            except ValueError:
                pass
        if "self_efficacy" in mot:
            try:
                motivation.self_efficacy = float(mot["self_efficacy"])
            except (TypeError, ValueError):
                pass
        if "goal_description" in mot:
            motivation.goal_description = str(mot["goal_description"])
        if any(
            k in mot
            for k in ("goal_type", "urgency", "self_efficacy", "goal_description")
        ):
            diff.motivation = motivation

        # Learning pace
        pace_data = self.extracted_features.get("learning_pace") or {}
        if pace_data:
            pace = PaceProfile()
            for k in (
                "avg_session_duration_min",
                "preferred_chunk_size_min",
                "review_interval_hours",
                "daily_time_budget_min",
                "sessions_per_week",
            ):
                if k in pace_data:
                    try:
                        setattr(pace, k, int(pace_data[k]))
                    except (TypeError, ValueError):
                        pass
            diff.learning_pace = pace

        # Modality preferences
        mod = self.extracted_features.get("modality") or {}
        if mod:
            modality = ModalityPreferences()
            for k, v in mod.items():
                if hasattr(modality, k):
                    try:
                        setattr(modality, k, float(v))
                    except (TypeError, ValueError):
                        pass
            diff.modality = modality

        # Metadata passthrough (merge with any existing metadata; later wins)
        meta = dict(self.extracted_features.get("metadata") or {})
        # Inject major / level into metadata so they survive into the profile.
        if "major" in self.extracted_features and self.extracted_features["major"]:
            meta["major"] = self.extracted_features["major"]
        if "level" in self.extracted_features and self.extracted_features["level"]:
            meta["level"] = self.extracted_features["level"]
        if meta:
            diff.metadata_merge.update(meta)

        return diff


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class ProfileBuilder:
    """High-level operations on :class:`LearnerProfile`."""

    def __init__(self, store: ProfileStore | None = None) -> None:
        self.store = store or get_profile_store()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialise the underlying store (call once at app startup)."""
        await self.store.init()

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, user_id: str) -> LearnerProfile:
        return await self.store.get_or_create(user_id)

    async def summary(self, user_id: str) -> dict[str, Any]:
        return await self.store.stats(user_id)

    async def history(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        events = await self.store.history(user_id, limit=limit)
        return [e.to_dict() for e in events]

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def create_blank(self, user_id: str) -> LearnerProfile:
        """Force-create a fresh blank profile (overwrites if exists)."""
        profile = empty_profile(user_id=user_id)
        return await self.store.replace(profile, source="builder.create_blank")

    async def ingest_signal(
        self,
        user_id: str,
        signal: DialogueSignal,
    ) -> tuple[LearnerProfile, ProfileDiff]:
        """Translate a dialogue signal into a diff and apply it."""
        diff = signal.to_diff()
        if diff.is_empty():
            profile = await self.get(user_id)
            return profile, diff
        profile = await self.store.apply_diff(
            user_id, diff, source=f"signal:{signal.confidence:.2f}"
        )
        return profile, diff

    async def ingest_exercise(
        self,
        user_id: str,
        result: ExerciseResult,
    ) -> tuple[LearnerProfile, ProfileDiff]:
        """Apply the result of one practice question."""
        diff = result.to_diff()
        profile = await self.store.apply_diff(
            user_id, diff, source=f"exercise:{result.concept}"
        )
        return profile, diff

    async def merge_diffs(
        self,
        user_id: str,
        diffs: Iterable[ProfileDiff],
        *,
        source: str = "merge",
    ) -> LearnerProfile:
        """Apply a batch of diffs in order. Returns the final profile."""
        merged = ProfileDiff()
        for d in diffs:
            # Knowledge deltas accumulate; sets overwrite; sub-objects last-wins.
            for k, v in d.knowledge_delta.items():
                merged.knowledge_delta[k] = merged.knowledge_delta.get(k, 0.0) + v
            merged.knowledge_set.update(d.knowledge_set)
            if d.cognitive_style is not None:
                merged.cognitive_style = d.cognitive_style
            if d.error_pattern is not None:
                merged.error_pattern = d.error_pattern  # last-wins per batch
            if d.learning_pace is not None:
                merged.learning_pace = d.learning_pace
            if d.motivation is not None:
                merged.motivation = d.motivation
            if d.modality is not None:
                merged.modality = d.modality
            merged.metadata_merge.update(d.metadata_merge)
        return await self.store.apply_diff(user_id, merged, source=source)

    def aggregate_events(
        self,
        profile: LearnerProfile,
        events: Iterable[LearningEvent],
        *,
        through_sequence: int,
    ) -> LearnerProfile:
        """Deterministically aggregate one stable event window."""
        updated = profile.model_copy(deep=True)
        scores: dict[str, list[float]] = defaultdict(list)
        formats: Counter[str] = Counter()
        for event in sorted(events, key=lambda item: (item.sequence, item.event_id)):
            if (
                event.event_type
                in {EventType.EXERCISE_ATTEMPTED, EventType.EXERCISE_SCORED}
                and event.score is not None
                and event.concept_id
            ):
                scores[event.concept_id].append(float(event.score))
            resource_format = str(event.metadata.get("resource_format") or "").strip()
            if resource_format:
                formats[resource_format] += 1

        confidence = dict(updated.metadata.get("concept_confidence") or {})
        evidence_counts = dict(updated.metadata.get("concept_evidence_count") or {})
        alpha = 0.4
        for concept, evidence in sorted(scores.items()):
            previous_count = int(evidence_counts.get(concept, 0))
            if previous_count and concept in updated.knowledge_map.scores:
                value = updated.knowledge_map.get(concept)
                remaining = evidence
            else:
                value = evidence[0]
                remaining = evidence[1:]
            for score in remaining:
                value = alpha * score + (1.0 - alpha) * value
            total_count = previous_count + len(evidence)
            updated.knowledge_map.set(concept, value)
            evidence_counts[concept] = total_count
            confidence[concept] = 1.0 - math.exp(-total_count / 3.0)

        updated.metadata["concept_evidence_count"] = evidence_counts
        updated.metadata["concept_confidence"] = confidence
        if formats:
            updated.metadata["preferred_resource_formats"] = [
                name for name, _ in sorted(formats.items(), key=lambda item: (-item[1], item[0]))
            ]
        updated.event_watermark = through_sequence
        return updated

    # ------------------------------------------------------------------
    # Aggregates / recommendations
    # ------------------------------------------------------------------

    def weak_concepts(
        self, profile: LearnerProfile, threshold: float = 0.4
    ) -> list[str]:
        return profile.weak_concepts(threshold)

    def strong_concepts(
        self, profile: LearnerProfile, threshold: float = 0.8
    ) -> list[str]:
        return profile.strong_concepts(threshold)

    def recommended_resource_types(
        self, profile: LearnerProfile, top_k: int = 3
    ) -> list[str]:
        """Pick the resource types this student would benefit from most.

        Strategy: rank modalities by preference × inverse mastery.
        Students with high modality preference AND weak knowledge in a
        related concept get more weight (the modality helps them learn it).
        """
        modality = profile.modality.model_dump()
        weakness = 1.0 - profile.knowledge_map.average_mastery() or 0.5
        # Modality -> possible resource type mapping
        type_links = {
            "diagram": "mindmap",
            "video": "video",
            "interactive": "exercise",
            "code": "code",
            "text": "document",
            "audio": "document",
            "exercise": "exercise",
        }
        scored: list[tuple[float, str]] = []
        for mod_name, mod_pref in modality.items():
            rtype = type_links.get(mod_name, "document")
            score = mod_pref * (0.5 + 0.5 * weakness)
            scored.append((score, rtype))
        scored.sort(reverse=True)
        seen: set[str] = set()
        out: list[str] = []
        for _score, rtype in scored:
            if rtype in seen:
                continue
            seen.add(rtype)
            out.append(rtype)
            if len(out) >= top_k:
                break
        return out

    def recommended_chunk_size(
        self, profile: LearnerProfile
    ) -> int:
        """Recommended chunk size in minutes based on pace + modality."""
        base = profile.learning_pace.preferred_chunk_size_min
        # Visual / video learners tend to chunk slightly longer
        if profile.cognitive_style in (CognitiveStyle.VISUAL,):
            return int(base * 1.2)
        if profile.cognitive_style in (CognitiveStyle.ACTIVE,):
            return max(5, int(base * 0.8))  # shorter, more interactive
        return base

    def mastery_breakdown(self, profile: LearnerProfile) -> dict[str, Any]:
        return {
            "average": round(profile.knowledge_map.average_mastery(), 3),
            "weak": profile.weak_concepts(),
            "strong": profile.strong_concepts(),
            "count": len(profile.knowledge_map.scores),
        }


_builder: ProfileBuilder | None = None
_builder_lock = threading.Lock()


def get_profile_builder() -> ProfileBuilder:
    """Return the singleton :class:`ProfileBuilder`."""
    global _builder
    if _builder is None:
        with _builder_lock:
            if _builder is None:
                _builder = ProfileBuilder()
    return _builder


def reset_profile_builder() -> None:
    """Clear the singleton. Tests only."""
    global _builder
    _builder = None


__all__ = [
    "DialogueSignal",
    "ExerciseResult",
    "ProfileBuilder",
    "get_profile_builder",
    "reset_profile_builder",
]
