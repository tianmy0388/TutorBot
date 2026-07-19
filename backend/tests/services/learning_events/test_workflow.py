from __future__ import annotations

import asyncio

import pytest
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.follow_up import ProfileUpdateFollowUpCapability
from tutor.services.jobs.schema import JobStatus
from tutor.services.jobs.store import JobStore
from tutor.services.learner_profile.store import ProfileStore
from tutor.services.learning_events.schema import EventType, LearningEvent
from tutor.services.learning_events.store import LearningEventStore
from tutor.services.learning_events.workflow import LearningWorkflow


@pytest.fixture
async def workflow(tmp_path):
    events = LearningEventStore(tmp_path / "events.db")
    profiles = ProfileStore(tmp_path / "profiles.db")
    jobs = JobStore(tmp_path / "jobs.db")
    await events.init()
    await profiles.init()
    await jobs.init()
    service = LearningWorkflow(
        event_store=events,
        profile_store=profiles,
        job_store=jobs,
    )
    yield service, events, profiles, jobs
    await events.close()
    await profiles.close()
    await jobs.close()


@pytest.mark.asyncio
async def test_first_scored_event_without_profile_schedules_profile_update(workflow):
    service, events, _, jobs = workflow
    appended = await events.append(
        LearningEvent(
            event_id="first-score",
            user_id="local-user",
            session_id="sess-loop",
            event_type=EventType.EXERCISE_SCORED,
            concept_id="backprop",
            score=0.0,
        )
    )

    children = await service.reconcile_user(
        "local-user",
        session_id="sess-loop",
        through_sequence=appended.event.sequence,
    )

    assert len(children) == 1
    assert children[0].task_kind == "profile_update"
    assert children[0].dedupe_key == "profile_update:0"
    assert children[0].metadata["through_sequence"] == appended.event.sequence
    root = await jobs.get(service.root_job_id("local-user"))
    assert root is not None


@pytest.mark.asyncio
async def test_profile_watermark_retains_five_score_batching(workflow):
    from tutor.services.learner_profile.schema import (
        PersistedLearningPath,
        empty_profile,
    )

    service, events, profiles, jobs = workflow
    profile = empty_profile("local-user")
    profile.version = 1
    await profiles.replace(profile, source="existing-profile")
    await profiles.save_path(
        PersistedLearningPath(user_id="local-user", profile_version=1)
    )
    for index in range(1, 7):
        appended = await events.append(
            LearningEvent(
                event_id=f"evt-{index}",
                user_id="local-user",
                session_id="sess-loop",
                event_type=EventType.EXERCISE_SCORED,
                concept_id="attention",
                score=index / 10,
            )
        )
        children = await service.reconcile_user(
            "local-user", session_id="sess-loop", through_sequence=appended.event.sequence
        )
        if index < 5:
            assert children == []

    root = await jobs.get(service.root_job_id("local-user"))
    assert root is not None and root.status == JobStatus.SUCCEEDED
    children = await jobs.get_children(root.job_id)
    assert len(children) == 1
    assert children[0].task_kind == "profile_update"
    assert children[0].dedupe_key == "profile_update:0"
    assert children[0].metadata["through_sequence"] == 5


@pytest.mark.asyncio
async def test_recent_exercise_evidence_is_bounded_scored_and_answer_safe(workflow):
    _, events, _, _ = workflow
    await events.append(
        LearningEvent(
            event_id="old-score",
            user_id="local-user",
            event_type=EventType.EXERCISE_SCORED,
            concept_id="chain_rule",
            score=0.25,
            metadata={
                "question_type": "short_answer",
                "answer_json": "private answer",
                "canonical_answer": "hidden answer",
                "hidden_tests": ["secret"],
            },
        )
    )
    await events.append(
        LearningEvent(
            event_id="not-scored",
            user_id="local-user",
            event_type=EventType.RESOURCE_VIEWED,
            concept_id="ignored",
            score=0.9,
        )
    )
    await events.append(
        LearningEvent(
            event_id="new-score",
            user_id="local-user",
            event_type=EventType.EXERCISE_SCORED,
            concept_id="backprop",
            score=0.0,
            metadata={"question_type": "single_choice", "answer_json": "B"},
        )
    )

    evidence = await events.recent_exercise_evidence("local-user", limit=1)

    assert evidence == [
        {
            "event_id": "new-score",
            "concept_id": "backprop",
            "score": 0.0,
            "question_type": "single_choice",
            "created_at": evidence[0]["created_at"],
        }
    ]
    assert "answer" not in str(evidence).casefold()
    assert "hidden" not in str(evidence).casefold()


@pytest.mark.asyncio
async def test_recent_evidence_rejects_malicious_question_type_metadata(workflow):
    _, events, _, _ = workflow
    secret = "SECRET_QUESTION_TYPE_PAYLOAD"
    await events.append(
        LearningEvent(
            event_id="malicious-question-type",
            user_id="local-user",
            event_type=EventType.EXERCISE_SCORED,
            concept_id="chain_rule",
            score=0.5,
            metadata={
                "question_type": {
                    "kind": "short_answer",
                    "secret": secret,
                }
            },
        )
    )

    evidence = await events.recent_exercise_evidence("local-user")

    assert evidence[0]["question_type"] == ""
    assert secret not in str(evidence)


@pytest.mark.asyncio
async def test_assessment_completion_triggers_immediately_and_survives_restart(workflow):
    service, events, _, jobs = workflow
    appended = await events.append(
        LearningEvent(
            event_id="assessment-1",
            user_id="local-user",
            session_id="sess-assessment",
            event_type=EventType.ASSESSMENT_COMPLETED,
            concept_id="attention",
            score=0.8,
        )
    )

    await service.reconcile_user("local-user", session_id="sess-assessment")
    fresh = LearningWorkflow(event_store=events, profile_store=service.profile_store, job_store=jobs)
    await fresh.reconcile_user("local-user", session_id="sess-assessment")

    root = await jobs.get(service.root_job_id("local-user"))
    children = await jobs.get_children(root.job_id)
    assert len(children) == 1
    assert children[0].metadata["through_sequence"] == appended.event.sequence


@pytest.mark.asyncio
async def test_learning_root_creation_is_idempotent_across_store_instances(tmp_path):
    db_path = tmp_path / "jobs.db"
    first = JobStore(db_path)
    second = JobStore(db_path)
    await first.init()
    await second.init()
    root = __import__("tutor.services.jobs.schema", fromlist=["Job"]).Job(
        job_id="learning-loop-stable",
        user_id="local-user",
        capability="learning_loop",
        status=JobStatus.SUCCEEDED,
    )

    a, b = await asyncio.gather(first.ensure_parent(root), second.ensure_parent(root))

    assert a.job_id == b.job_id
    assert await first.count("local-user") == 1
    await first.close()
    await second.close()


@pytest.mark.asyncio
async def test_reconcile_all_repairs_event_to_job_crash_window(workflow):
    service, events, _, jobs = workflow
    for index in range(5):
        await events.append(
            LearningEvent(
                event_id=f"crash-{index}",
                user_id="local-user",
                event_type=EventType.EXERCISE_SCORED,
                concept_id="attention",
                score=0.5,
            )
        )
    assert await jobs.get(service.root_job_id("local-user")) is None

    repaired = await service.reconcile_all()

    assert repaired == 1
    root = await jobs.get(service.root_job_id("local-user"))
    assert len(await jobs.get_children(root.job_id)) == 1


@pytest.mark.asyncio
async def test_reconcile_repairs_missing_current_profile_path_once(workflow):
    from tutor.core.capability_result import FollowUpTaskSpec
    from tutor.services.jobs.follow_up import FollowUpScheduler
    from tutor.services.jobs.schema import Job
    from tutor.services.learner_profile.schema import empty_profile

    service, _, profiles, jobs = workflow
    profile = empty_profile("local-user")
    profile.version = 2
    profile.event_watermark = 6
    profile.knowledge_map.set("attention", 0.6)
    await profiles.replace(profile, source="migration-recovery-fixture")
    root = await jobs.ensure_parent(
        Job(
            job_id=service.root_job_id("local-user"),
            user_id="local-user",
            session_id="sess-recovery",
            capability="learning_loop",
            status=JobStatus.SUCCEEDED,
        )
    )
    failed = (
        await FollowUpScheduler(jobs).enqueue(
            root.job_id,
            (
                FollowUpTaskSpec(
                    kind="path_rebuild",
                    dedupe_key="path_rebuild:2",
                    payload={
                        "user_id": "local-user",
                        "profile_version": 2,
                        "profile": profile.model_dump(mode="json"),
                    },
                ),
            ),
        )
    )[0]
    await jobs.update_status(failed.job_id, status=JobStatus.FAILED, error="old failure")

    first = await service.reconcile_user("local-user", session_id="sess-recovery")
    second = await service.reconcile_user("local-user", session_id="sess-recovery")

    assert len(first) == 1
    assert first[0].dedupe_key == "path_rebuild:2:recovery-1"
    assert first[0].status == JobStatus.PENDING
    assert second[0].job_id == first[0].job_id
    all_children = await jobs.get_children(root.job_id)
    assert [child.dedupe_key for child in all_children] == [
        "path_rebuild:2",
        "path_rebuild:2:recovery-1",
    ]


@pytest.mark.asyncio
async def test_reconcile_all_includes_profile_users_without_events(workflow):
    from tutor.services.learner_profile.schema import empty_profile

    service, _, profiles, jobs = workflow
    profile = empty_profile("profile-only-user")
    profile.version = 3
    await profiles.replace(profile, source="profile-only-fixture")

    assert await service.reconcile_all() == 1
    root = await jobs.get(service.root_job_id("profile-only-user"))
    assert root is not None
    children = await jobs.get_children(root.job_id)
    assert len(children) == 1
    assert children[0].dedupe_key == "path_rebuild:3"


@pytest.mark.asyncio
async def test_path_child_is_globally_deduped_by_user_and_profile_version(workflow):
    from tutor.core.capability_result import FollowUpTaskSpec
    from tutor.services.jobs.follow_up import FollowUpScheduler
    from tutor.services.jobs.schema import Job

    _, _, _, jobs = workflow
    parents = [
        await jobs.ensure_parent(
            Job(
                job_id=f"profile-parent-{index}",
                user_id="local-user",
                capability="profile_update",
                status=JobStatus.SUCCEEDED,
            )
        )
        for index in (1, 2)
    ]
    spec = FollowUpTaskSpec(
        kind="path_rebuild",
        dedupe_key="path_rebuild:2",
        payload={
            "user_id": "local-user",
            "profile_version": 2,
            "profile": {"user_id": "local-user", "version": 2},
        },
    )

    first = (await FollowUpScheduler(jobs).enqueue(parents[0].job_id, (spec,)))[0]
    second = (await FollowUpScheduler(jobs).enqueue(parents[1].job_id, (spec,)))[0]

    assert first.job_id == second.job_id


@pytest.mark.asyncio
async def test_default_reconcile_stops_at_earliest_threshold_in_latest_snapshot(
    workflow,
):
    from tutor.services.learner_profile.schema import (
        PersistedLearningPath,
        empty_profile,
    )

    service, events, profiles, jobs = workflow
    profile = empty_profile("local-user")
    await profiles.replace(profile, source="existing-profile")
    await profiles.save_path(
        PersistedLearningPath(user_id="local-user", profile_version=1)
    )
    sequences = []
    for index in range(6):
        appended = await events.append(
            LearningEvent(
                event_id=f"concurrent-{index}",
                user_id="local-user",
                event_type=EventType.EXERCISE_SCORED,
                concept_id="attention",
                score=0.5,
            )
        )
        sequences.append(appended.event.sequence)

    await service.reconcile_user("local-user")

    root = await jobs.get(service.root_job_id("local-user"))
    child = (await jobs.get_children(root.job_id))[0]
    assert child.metadata["through_sequence"] == sequences[4]


@pytest.mark.asyncio
async def test_explicit_reconcile_boundary_never_absorbs_later_events(workflow):
    from tutor.services.learner_profile.schema import (
        PersistedLearningPath,
        empty_profile,
    )

    service, events, profiles, jobs = workflow
    profile = empty_profile("local-user")
    await profiles.replace(profile, source="existing-profile")
    await profiles.save_path(
        PersistedLearningPath(user_id="local-user", profile_version=1)
    )
    sequences = []
    for index in range(6):
        appended = await events.append(
            LearningEvent(
                event_id=f"fixed-window-{index}",
                user_id="local-user",
                event_type=EventType.EXERCISE_SCORED,
                concept_id="attention",
                score=0.5,
            )
        )
        sequences.append(appended.event.sequence)

    assert await service.reconcile_user(
        "local-user", through_sequence=sequences[3]
    ) == []
    children = await service.reconcile_user(
        "local-user", through_sequence=sequences[4]
    )

    assert len(children) == 1
    assert children[0].metadata["through_sequence"] == sequences[4]
    root = await jobs.get(service.root_job_id("local-user"))
    assert len(await jobs.get_children(root.job_id)) == 1


@pytest.mark.asyncio
async def test_only_scored_exercises_with_scores_count_toward_threshold(workflow):
    service, events, _, jobs = workflow
    for index in range(5):
        await events.append(
            LearningEvent(
                event_id=f"resource-with-score-{index}",
                user_id="local-user",
                event_type=EventType.RESOURCE_VIEWED,
                score=0.9,
            )
        )
        await events.append(
            LearningEvent(
                event_id=f"unscored-exercise-{index}",
                user_id="local-user",
                event_type=EventType.EXERCISE_SCORED,
                concept_id="attention",
                score=None,
            )
        )

    assert await service.reconcile_user("local-user") == []
    assert await jobs.get(service.root_job_id("local-user")) is None


@pytest.mark.asyncio
async def test_profile_child_chains_next_fixed_window_without_eleventh_event(workflow):
    service, events, profiles, jobs = workflow
    for index in range(5):
        appended = await events.append(
            LearningEvent(
                event_id=f"first-window-{index}",
                user_id="local-user",
                event_type=EventType.EXERCISE_SCORED,
                concept_id="attention",
                score=0.4,
            )
        )
        await service.reconcile_user(
            "local-user", through_sequence=appended.event.sequence
        )
    root = await jobs.get(service.root_job_id("local-user"))
    first_child = (await jobs.get_children(root.job_id))[0]

    for index in range(5, 11):
        appended = await events.append(
            LearningEvent(
                event_id=f"second-window-{index}",
                user_id="local-user",
                event_type=EventType.EXERCISE_SCORED,
                concept_id="attention",
                score=0.8,
            )
        )
        duplicate = await service.reconcile_user(
            "local-user", through_sequence=appended.event.sequence
        )
        assert duplicate[0].job_id == first_child.job_id

    result = await ProfileUpdateFollowUpCapability(
        event_store=events,
        profile_store=profiles,
    ).run(
        UnifiedContext(user_id="local-user", metadata=dict(first_child.metadata)),
        StreamBus(),
    )

    next_profiles = [
        spec for spec in result.follow_up_tasks if spec.kind == "profile_update"
    ]
    assert len(next_profiles) == 1
    assert next_profiles[0].dedupe_key == "profile_update:1"
    assert next_profiles[0].payload["from_watermark"] == 1
    assert next_profiles[0].payload["through_sequence"] == 6
