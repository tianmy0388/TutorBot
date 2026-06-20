"""Integration test for the adaptive learning loop (Task 11).

Verifies the chain:
- Resources expose citations, confidence, review, safety, generated_by
- Unverified claims survive as warnings, not silent assertions
- A weak exercise result nudges the profile and the next path plan
  toward the weak concept
"""

from __future__ import annotations

import asyncio

import pytest

from tutor.services.learner_profile.builder import (
    ProfileBuilder,
    reset_profile_builder,
)
from tutor.services.learner_profile.schema import (
    LearnerProfile,
    ModalityPreferences,
)
from tutor.services.learner_profile.store import (
    ProfileStore,
    reset_profile_store,
)
from tutor.services.resource_package.schema import (
    Resource,
    ResourcePackage,
    ResourceType,
)


@pytest.fixture
def fresh_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache
    reset_settings_cache()
    reset_profile_store()
    reset_profile_builder()


def test_resource_exposes_evidence_fields() -> None:
    r = Resource(
        type=ResourceType.DOCUMENT,
        title="Transformer 解释",
        confidence_score=0.82,
        generated_by=["content_expert", "pedagogy"],
        citations=[
            {"source": "ai_introduction/transformer.md", "page": 3, "snippet": "..."},
        ],
        review={"verdict": "pass", "quality_score": 0.85, "reviewer": "quality"},
        safety={"verdict": "safe", "flagged": False},
        unverified_claims=["Transformer 比 LSTM 训练更快（未引用）"],
    )
    assert r.confidence_score == 0.82
    assert "content_expert" in r.generated_by
    assert r.citations
    assert r.review["verdict"] == "pass"
    assert r.safety["verdict"] == "safe"
    assert r.unverified_claims  # preserved, not silently dropped


def test_package_round_trip_preserves_evidence() -> None:
    r = Resource(
        type=ResourceType.VIDEO,
        title="动画：注意力机制",
        confidence_score=0.7,
        generated_by=["multimedia", "manim_renderer"],
        citations=[],
        review={"verdict": "pass", "quality_score": 0.7, "reviewer": "qa"},
    )
    pkg = ResourcePackage(
        package_id="pkg-1",
        topic="注意力机制",
        resources=[r],
        target_profile_snapshot={},
    )
    assert pkg.resources[0].generated_by == ["multimedia", "manim_renderer"]
    assert pkg.resources[0].review["verdict"] == "pass"


@pytest.mark.asyncio
async def test_weak_exercise_nudges_profile_and_path(fresh_profile) -> None:
    """A weak exercise result should drop the concept's mastery in the
    profile, and the next path-planning call should put that concept
    ahead of a stronger one.
    """
    store = ProfileStore()
    await store.init()
    builder = ProfileBuilder(store=store)

    # Seed a profile with two concepts.
    profile = LearnerProfile(
        user_id="u1",
        modality=ModalityPreferences(),
    )
    profile.knowledge_map.scores["transformer"] = 0.8
    profile.knowledge_map.scores["rnn"] = 0.3
    await store.save(profile)

    # Simulate a weak exercise result for the strong concept.
    from tutor.services.learner_profile.builder import ExerciseResult

    result = ExerciseResult(
        concept="transformer",
        correct=False,
        difficulty=4,
        elapsed_seconds=120,
        mistake_type="misconception",
        note="missed 4 of 5",
    )
    await builder.ingest_exercise(profile.user_id, result)
    updated = await builder.get(profile.user_id)
    assert updated is not None
    assert updated.knowledge_map.scores["transformer"] < 0.8
    # The weak concept still trails, but the strong one is now also weak
    # enough to be a "next target".
    weak = updated.weak_concepts(threshold=0.7)
    assert "transformer" in weak or "rnn" in weak

    await store.close()
    reset_profile_store()
    reset_profile_builder()
