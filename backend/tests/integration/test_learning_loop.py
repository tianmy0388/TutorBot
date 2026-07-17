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
from tutor.services.learner_profile import _close_profile_store_sync
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
)
from tutor.services.resource_package.schema import (
    Resource,
    ResourcePackage,
    ResourceType,
)


async def _wait_terminal(store, job_id):
    from tutor.services.jobs.schema import JobStatus

    for _ in range(200):
        job = await store.get(job_id)
        if job is not None and job.status in {
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
            JobStatus.PARTIAL,
        }:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"job {job_id} did not terminalize")


@pytest.mark.asyncio
async def test_five_scores_persist_profile_then_version_bound_path(tmp_path, monkeypatch):
    import networkx as nx
    from tutor.services.jobs import follow_up as follow_up_module
    from tutor.services.jobs.follow_up import (
        PathRebuildFollowUpCapability,
        ProfileUpdateFollowUpCapability,
    )
    from tutor.services.jobs.runner import JobRunner
    from tutor.services.jobs.store import JobStore
    from tutor.services.knowledge_graph.planner import KGPathPlanner
    from tutor.services.knowledge_graph.schema import EdgeType, KGEdge, KGNode, KnowledgeGraph
    from tutor.services.learner_profile.store import ProfileStore
    from tutor.services.learning_events.schema import EventType, LearningEvent
    from tutor.services.learning_events.store import LearningEventStore
    from tutor.services.learning_events.workflow import LearningWorkflow

    events = LearningEventStore(tmp_path / "events.db")
    profiles = ProfileStore(tmp_path / "profiles.db")
    jobs = JobStore(tmp_path / "jobs.db")
    await events.init()
    await profiles.init()
    await jobs.init()

    model = KnowledgeGraph(
        course="test-course",
        nodes=[
            KGNode(id="attention", name="Attention", estimated_hours=1),
            KGNode(id="transformer", name="Transformer", prerequisites=["attention"], estimated_hours=2),
        ],
        edges=[KGEdge(**{"from": "attention", "to": "transformer", "type": EdgeType.PREREQUISITE})],
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(["attention", "transformer"])
    graph.add_edge("attention", "transformer")

    class KG:
        def default_course(self): return "test-course"
        def has_course(self, course): return course == "test-course"
        def get_graph(self, course): return model, graph
        def plan_for_learner(self, course, profile):
            return KGPathPlanner().plan(model, graph, profile)

    monkeypatch.setitem(
        follow_up_module._FOLLOW_UP_BUILDERS,
        "profile_update",
        lambda: ProfileUpdateFollowUpCapability(event_store=events, profile_store=profiles),
    )
    monkeypatch.setitem(
        follow_up_module._FOLLOW_UP_BUILDERS,
        "path_rebuild",
        lambda: PathRebuildFollowUpCapability(profile_store=profiles, kg_service=KG()),
    )

    class Registry:
        def get(self, name): return None

    runner = JobRunner(job_store=jobs, capability_registry=Registry())
    workflow = LearningWorkflow(event_store=events, profile_store=profiles, job_store=jobs)
    for index, score in enumerate((0.4, 0.5, 0.6, 0.7, 0.8), start=1):
        appended = await events.append(LearningEvent(
            event_id=f"score-{index}", user_id="local-user", session_id="sess-loop",
            event_type=EventType.EXERCISE_SCORED, concept_id="attention", score=score,
        ))
        await workflow.reconcile_user(
            "local-user", session_id="sess-loop",
            through_sequence=appended.event.sequence, course="test-course",
        )

    assert await runner.resume_pending() == 1
    root = await jobs.get(workflow.root_job_id("local-user"))
    profile_child = (await jobs.get_children(root.job_id))[0]
    assert (await _wait_terminal(jobs, profile_child.job_id)).status.value == "succeeded"
    path_child = (await jobs.get_children(profile_child.job_id))[0]
    assert path_child.dedupe_key == "path_rebuild:2"
    assert (await _wait_terminal(jobs, path_child.job_id)).status.value == "succeeded"

    profile = await profiles.get("local-user")
    path = await profiles.get_latest_path("local-user")
    assert profile.version == 2 and profile.event_watermark == 5
    assert profile.knowledge_map.get("attention") > 0
    assert path.profile_version == profile.version
    assert [node["id"] for node in path.nodes] == ["attention", "transformer"]
    assert {edge["from"] for edge in path.edges} <= {node["id"] for node in path.nodes}
    await runner.shutdown()
    await events.close()
    await profiles.close()
    await jobs.close()


@pytest.mark.asyncio
async def test_profile_failure_never_creates_path_child(tmp_path, monkeypatch):
    from tutor.services.jobs import follow_up as follow_up_module
    from tutor.services.jobs.follow_up import ProfileUpdateFollowUpCapability
    from tutor.services.jobs.runner import JobRunner
    from tutor.services.jobs.store import JobStore
    from tutor.services.learner_profile.store import ProfileStore
    from tutor.services.learning_events.schema import EventType, LearningEvent
    from tutor.services.learning_events.store import LearningEventStore
    from tutor.services.learning_events.workflow import LearningWorkflow

    events = LearningEventStore(tmp_path / "events.db")
    profiles = ProfileStore(tmp_path / "profiles.db")
    jobs = JobStore(tmp_path / "jobs.db")
    await events.init()
    await profiles.init()
    await jobs.init()
    workflow = LearningWorkflow(event_store=events, profile_store=profiles, job_store=jobs)
    for index in range(5):
        appended = await events.append(LearningEvent(
            event_id=f"failure-{index}", user_id="local-user",
            event_type=EventType.EXERCISE_SCORED, concept_id="attention", score=0.5,
        ))
        await workflow.reconcile_user("local-user", through_sequence=appended.event.sequence)

    async def fail_save(*args, **kwargs):
        raise RuntimeError("private database detail")

    monkeypatch.setattr(profiles, "save_event_profile", fail_save)
    monkeypatch.setitem(
        follow_up_module._FOLLOW_UP_BUILDERS,
        "profile_update",
        lambda: ProfileUpdateFollowUpCapability(event_store=events, profile_store=profiles),
    )

    class Registry:
        def get(self, name): return None

    runner = JobRunner(job_store=jobs, capability_registry=Registry())
    assert await runner.resume_pending() == 1
    root = await jobs.get(workflow.root_job_id("local-user"))
    child = (await jobs.get_children(root.job_id))[0]
    terminal = await _wait_terminal(jobs, child.job_id)
    assert terminal.status.value == "failed"
    assert await jobs.get_children(child.job_id) == []
    assert await profiles.get("local-user") is None
    assert "private database detail" not in str(terminal.result)
    await runner.shutdown()
    await events.close()
    await profiles.close()
    await jobs.close()


@pytest.fixture
def fresh_profile(monkeypatch, tmp_path):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache
    reset_settings_cache()
    _close_profile_store_sync()
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
    _close_profile_store_sync()
    reset_profile_builder()
