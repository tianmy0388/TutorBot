"""Cross-system integration coverage for the local-validation follow-up.

Two durable chains are exercised end to end with in-memory fakes only at the
LLM/renderer boundary:

1. A complete exercise resource passes through the runner's schema-aware
   public projection (options survive without ``[TRUNCATED]``), a draft is
   saved without publishing learning evidence, the explicit submission clears
   the draft atomically and publishes one durable ``EXERCISE_SCORED`` event,
   and the first scored event schedules the profile/path child jobs.
2. A terminally failed Manim video resource is repaired through one durable
   ``video_repair_render`` child: a mocked full-regeneration agent returns a
   complete replacement program which is validated and rendered, and both the
   job and the resource reach the expected terminal state (ready on success,
   preserved failure on render error).
"""

from __future__ import annotations

import asyncio
import json
import sys

import pytest
from tutor.core.capability_result import FollowUpTaskSpec
from tutor.services.jobs.follow_up import (
    FollowUpScheduler,
    VideoRepairFollowUpCapability,
)
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import Job, JobStatus
from tutor.services.jobs.store import JobStore
from tutor.services.manim_render.service import RenderedVideo
from tutor.services.resource_package.public_projection import project_public_event
from tutor.services.resource_package.schema import (
    Resource,
    ResourcePackage,
    ResourceType,
)
from tutor.services.resource_package.store import ResourcePackageStore

TERMINAL = {
    JobStatus.SUCCEEDED,
    JobStatus.FAILED,
    JobStatus.CANCELLED,
    JobStatus.PARTIAL,
}


async def _wait_terminal(store: JobStore, job_id: str) -> Job:
    for _ in range(12_000):
        job = await store.get(job_id)
        if job is not None and job.status in TERMINAL:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError(f"job did not terminalize: {job_id}")


class _Capabilities:
    def get(self, name: str):
        return None


# ---------------------------------------------------------------------------
# 1. Exercise: projection -> draft -> submit -> scored event -> profile/path
# ---------------------------------------------------------------------------


def _exercise_resource() -> Resource:
    return Resource(
        resource_id="exercise-follow-up",
        type=ResourceType.EXERCISE,
        title="注意力机制练习",
        content="检验对注意力机制的理解。",
        format_specific={
            "questions": [
                {
                    "id": "q-attention",
                    "type": "single_choice",
                    "difficulty": 2,
                    "knowledge_point": "注意力",
                    "question": "自注意力中 Q、K、V 分别来自哪里？",
                    "options": [
                        {"label": "A", "text": "三个不同的输入序列"},
                        {"label": "B", "text": "同一输入的三种线性投影"},
                        {"label": "C", "text": "三个独立的注意力头"},
                        {"label": "D", "text": "编码器与解码器"},
                    ],
                    "explanation": "Q、K、V 都是同一输入经过不同线性变换得到的。",
                }
            ],
            "total_questions": 1,
        },
    )


@pytest.mark.asyncio
async def test_exercise_projection_draft_submit_event_and_profile_path(
    tmp_path, monkeypatch
):
    import networkx as nx
    from tutor.services.exercise_responses.publisher import publish_submission_event
    from tutor.services.exercise_responses.schema import (
        ExerciseDraft,
        ExerciseSubmission,
    )
    from tutor.services.exercise_responses.store import ExerciseResponseStore
    from tutor.services.jobs import follow_up as follow_up_module
    from tutor.services.jobs.follow_up import (
        PathRebuildFollowUpCapability,
        ProfileUpdateFollowUpCapability,
    )
    from tutor.services.knowledge_graph.planner import KGPathPlanner
    from tutor.services.knowledge_graph.schema import (
        EdgeType,
        KGEdge,
        KGNode,
        KnowledgeGraph,
    )
    from tutor.services.learner_profile.store import ProfileStore
    from tutor.services.learning_events.schema import EventType
    from tutor.services.learning_events.store import LearningEventStore
    from tutor.services.learning_events.workflow import LearningWorkflow

    # --- runner projection: options survive the public event intact ---------
    resource = _exercise_resource()
    projected = project_public_event(
        {
            "type": "resource",
            "content": "",
            "metadata": {"resource": resource.model_dump(mode="json")},
        }
    )
    projected_options = projected["metadata"]["resource"]["format_specific"][
        "questions"
    ][0]["options"]
    assert projected_options == resource.format_specific["questions"][0]["options"]
    assert "[TRUNCATED]" not in json.dumps(projected, ensure_ascii=False)

    # --- durable stores ------------------------------------------------------
    events = LearningEventStore(tmp_path / "events.db")
    responses = ExerciseResponseStore(tmp_path / "responses.db")
    profiles = ProfileStore(tmp_path / "profiles.db")
    jobs = JobStore(tmp_path / "jobs.db")
    await events.init()
    await responses.init()
    await profiles.init()
    await jobs.init()

    model = KnowledgeGraph(
        course="test-course",
        nodes=[
            KGNode(id="attention", name="Attention", estimated_hours=1),
            KGNode(
                id="transformer",
                name="Transformer",
                prerequisites=["attention"],
                estimated_hours=2,
            ),
        ],
        edges=[
            KGEdge(
                **{
                    "from": "attention",
                    "to": "transformer",
                    "type": EdgeType.PREREQUISITE,
                }
            )
        ],
    )
    graph = nx.DiGraph()
    graph.add_nodes_from(["attention", "transformer"])
    graph.add_edge("attention", "transformer")

    class KG:
        def default_course(self):
            return "test-course"

        def has_course(self, course):
            return course == "test-course"

        def get_graph(self, course):
            return model, graph

        def plan_for_learner(self, course, profile):
            return KGPathPlanner().plan(model, graph, profile)

    monkeypatch.setitem(
        follow_up_module._FOLLOW_UP_BUILDERS,
        "profile_update",
        lambda: ProfileUpdateFollowUpCapability(
            event_store=events, profile_store=profiles
        ),
    )
    monkeypatch.setitem(
        follow_up_module._FOLLOW_UP_BUILDERS,
        "path_rebuild",
        lambda: PathRebuildFollowUpCapability(
            profile_store=profiles, kg_service=KG()
        ),
    )

    runner = JobRunner(job_store=jobs, capability_registry=_Capabilities())
    workflow = LearningWorkflow(
        event_store=events, profile_store=profiles, job_store=jobs
    )

    # --- draft save: replaceable, and never learning evidence ----------------
    await responses.upsert_draft(
        ExerciseDraft(
            user_id="local-user",
            package_id="pkg-follow-up",
            resource_id=resource.resource_id,
            question_id="q-attention",
            question_type="single_choice",
            answer_json="A",
        )
    )
    await responses.upsert_draft(
        ExerciseDraft(
            user_id="local-user",
            package_id="pkg-follow-up",
            resource_id=resource.resource_id,
            question_id="q-attention",
            question_type="single_choice",
            answer_json="B",
        )
    )
    state = await responses.get_state(
        "local-user", "pkg-follow-up", resource.resource_id, "q-attention"
    )
    assert state.draft is not None
    assert state.draft.answer_json == "B"
    assert state.submissions == []
    assert await events.query("local-user", event_types=[EventType.EXERCISE_SCORED]) == []

    # --- submit: draft cleared atomically, durable scored event published -----
    durable = await responses.save_submission(
        ExerciseSubmission(
            submission_id="follow-up-submission",
            client_submission_id="follow-up-client-1",
            user_id="local-user",
            session_id="sess-follow-up",
            package_id="pkg-follow-up",
            resource_id=resource.resource_id,
            question_id="q-attention",
            question_type="single_choice",
            answer_json="B",
            correct=True,
            score=1.0,
            concept_id="attention",
            course="test-course",
        )
    )
    state = await responses.get_state(
        "local-user", "pkg-follow-up", resource.resource_id, "q-attention"
    )
    assert state.draft is None
    assert [submission.submission_id for submission in state.submissions] == [
        "follow-up-submission"
    ]

    assert await publish_submission_event(
        durable,
        response_store=responses,
        workflow=workflow,
    )
    scored = await events.query("local-user", event_types=[EventType.EXERCISE_SCORED])
    assert [event.event_id for event in scored] == [
        "exercise-response:follow-up-submission"
    ]
    persisted = await responses.get_submission_for_user(
        "follow-up-submission", "local-user"
    )
    assert persisted is not None and persisted.event_published

    # --- first scored event schedules profile update and version-bound path ---
    assert await runner.resume_pending() == 1
    root = await jobs.get(workflow.root_job_id("local-user"))
    profile_child = (await jobs.get_children(root.job_id))[0]
    assert (await _wait_terminal(jobs, profile_child.job_id)).status.value == (
        "succeeded"
    )
    path_child = (await jobs.get_children(profile_child.job_id))[0]
    assert path_child.dedupe_key == "path_rebuild:2"
    assert (await _wait_terminal(jobs, path_child.job_id)).status.value == "succeeded"

    profile = await profiles.get("local-user")
    path = await profiles.get_latest_path("local-user")
    assert profile.version == 2 and profile.event_watermark == 1
    assert profile.knowledge_map.get("attention") == 1.0
    assert path.profile_version == profile.version
    assert [node["id"] for node in path.nodes] == ["attention", "transformer"]
    await runner.shutdown()
    await events.close()
    await responses.close()
    await profiles.close()
    await jobs.close()


# ---------------------------------------------------------------------------
# 2. Failed Manim video -> durable mocked full-regeneration child -> terminal
# ---------------------------------------------------------------------------


_BROKEN_MANIM = (
    "from manim import *\n"
    "class MainScene(Scene):\n"
    "    def construct(self):\n"
    '        self.add(SVGMobject("person_silhouette.svg"))\n'
)

_REPAIRED_MANIM = (
    "from manim import *\n"
    "\n"
    "class MainScene(Scene):\n"
    "    def construct(self):\n"
    "        dot = Dot()\n"
    "        self.play(Create(dot), run_time=0.5)\n"
)


class _ScriptedRepairAgent:
    """Return queued complete replacement programs, recording every call."""

    def __init__(self, candidates: list[str]) -> None:
        self.candidates = list(candidates)
        self.calls: list[tuple[str, object, object]] = []

    async def regenerate(self, context, failed_code, failure, runtime):
        self.calls.append((failed_code, failure, runtime))
        if not self.candidates:
            raise RuntimeError("no candidate available")
        return self.candidates.pop(0)


class _FakeRepairRenderer:
    def __init__(self, result: RenderedVideo) -> None:
        self.result = result
        self.calls = 0

    async def render(self, **kwargs):
        self.calls += 1
        return self.result


async def _failed_video_fixture(tmp_path):
    jobs = JobStore(tmp_path / "jobs.db")
    packages = ResourcePackageStore(tmp_path / "packages.db")
    await jobs.init()
    await packages.init()
    parent = Job(
        job_id="parent-repair",
        user_id="local-user",
        session_id="session-repair",
        capability="resource_generation",
        status=JobStatus.SUCCEEDED,
    )
    await jobs.save(parent)
    resource = Resource(
        resource_id="repair-video-1",
        type=ResourceType.VIDEO,
        title="Failed video",
        format_specific={
            "manim_code": _BROKEN_MANIM,
            "scene_class": "MainScene",
            "render_status": "failed",
            "render_error_code": "missing_external_asset",
            "render_error": "Missing required asset files: person_silhouette.svg",
            "render_failure": {
                "error_code": "missing_external_asset",
                "summary": "Missing required asset files: person_silhouette.svg",
                "traceback_tail": ["FileNotFoundError: person_silhouette.svg"],
                "log_artifact_key": "manim_logs/initial/attempt-01.log",
            },
            "source_revision": 0,
        },
    )
    package = ResourcePackage(
        package_id="package-repair",
        topic="topic",
        resources=[resource],
    )
    package.associate_originating_job(parent.job_id)
    package.metadata["session_id"] = parent.session_id
    await packages.save(package, user_id=parent.user_id)
    child = (
        await FollowUpScheduler(jobs).enqueue(
            parent.job_id,
            (
                FollowUpTaskSpec(
                    kind="video_repair_render",
                    dedupe_key="video-repair:package-repair:repair-video-1:0:1",
                    payload={
                        "package_id": package.package_id,
                        "resource_id": resource.resource_id,
                        "user_id": parent.user_id,
                        "failed_revision": 0,
                        "expected_repair_job_id": None,
                    },
                ),
            ),
        )
    )[0]
    return jobs, packages, parent, child


def _repair_runner(jobs, packages, agent, renderer) -> JobRunner:
    return JobRunner(
        job_store=jobs,
        capability_registry=_Capabilities(),  # type: ignore[arg-type]
        follow_up_builder=lambda kind: VideoRepairFollowUpCapability(
            package_store=packages,
            repair_agent=agent,
            render_service=renderer,
            runtime_namespace={
                "Scene": object(),
                "Dot": object(),
                "Create": object(),
            },
            runtime_versions={"python": "3.11", "manim": "0.20"},
        ),
    )


@pytest.mark.asyncio
async def test_failed_video_durable_repair_child_succeeds_to_ready(
    tmp_path, monkeypatch
):
    from tutor.services.config.settings import get_settings

    jobs, packages, _parent, child = await _failed_video_fixture(tmp_path)
    data_dir = tmp_path / "data"
    video = data_dir / "manim_videos" / "MainScene.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"non-empty-repaired-mp4")
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)
    agent = _ScriptedRepairAgent([_REPAIRED_MANIM])
    renderer = _FakeRepairRenderer(
        RenderedVideo(
            success=True,
            code=_REPAIRED_MANIM,
            video_path=video,
            public_url="/static/manim/MainScene.mp4",
            attempts=1,
        )
    )
    runner = _repair_runner(jobs, packages, agent, renderer)

    assert await runner.resume_pending() == 1
    terminal = await _wait_terminal(jobs, child.job_id)
    persisted = await packages.get_resource("repair-video-1")

    assert terminal.status == JobStatus.SUCCEEDED
    assert sum(event.get("type") == "job_terminal" for event in terminal.events) == 1
    assert len(agent.calls) == 1
    # The repair prompt received the complete failed source plus diagnostics.
    assert agent.calls[0][0] == _BROKEN_MANIM
    assert renderer.calls == 1
    assert persisted is not None
    fields = persisted.format_specific
    assert fields["render_status"] == "ready"
    assert fields["repair_status"] == "ready"
    assert fields["manim_code"] == _REPAIRED_MANIM
    assert fields["source_revision"] == 1
    assert fields["artifact_key"] == "manim_videos/MainScene.mp4"
    assert fields["video_url"] == "/static/manim/MainScene.mp4"
    assert "render_failure" not in fields
    assert "render_error" not in fields
    assert fields["repair_history"][-1]["status"] == "ready"
    assert fields["repair_history"][-1]["job_id"] == child.job_id
    await runner.shutdown()
    await jobs.close()
    await packages.close()


@pytest.mark.asyncio
async def test_failed_video_durable_repair_child_failure_preserves_original(
    tmp_path, monkeypatch
):
    from tutor.services.config.settings import get_settings
    from tutor.services.manim_render.executor import RenderFailure

    jobs, packages, _parent, child = await _failed_video_fixture(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)
    agent = _ScriptedRepairAgent([_REPAIRED_MANIM])
    failure = RenderFailure(
        error_code="process_exit",
        summary="Manim exited with code 1",
        traceback_tail=("ValueError: still broken",),
        log_artifact_key="manim_logs/repair/attempt-01.log",
    )
    renderer = _FakeRepairRenderer(
        RenderedVideo(
            success=False,
            code=_REPAIRED_MANIM,
            attempts=1,
            error=failure.summary,
            failure=failure,
        )
    )
    runner = _repair_runner(jobs, packages, agent, renderer)

    assert await runner.resume_pending() == 1
    terminal = await _wait_terminal(jobs, child.job_id)
    persisted = await packages.get_resource("repair-video-1")

    assert terminal.status == JobStatus.FAILED
    assert sum(event.get("type") == "job_terminal" for event in terminal.events) == 1
    assert len(agent.calls) == 1
    assert renderer.calls == 1
    assert persisted is not None
    fields = persisted.format_specific
    # The failed regeneration never overwrites the last syntactically valid
    # source or the original public render failure.
    assert fields["render_status"] == "failed"
    assert fields["repair_status"] == "failed"
    assert fields["manim_code"] == _BROKEN_MANIM
    assert fields["source_revision"] == 0
    assert fields["render_error_code"] == "missing_external_asset"
    assert fields["render_error"] == (
        "Missing required asset files: person_silhouette.svg"
    )
    assert "video_url" not in fields
    latest = fields["repair_history"][-1]
    assert latest["status"] == "failed"
    assert latest["job_id"] == child.job_id
    assert latest["error_code"] == "process_exit"
    # The latest failed candidate is retained privately for the next manual
    # repair attempt (never rendered by the frontend).
    assert fields["repair_candidate_code"] == _REPAIRED_MANIM
    assert fields["repair_candidate_failure"]["error_code"] == "process_exit"
    await runner.shutdown()
    await jobs.close()
    await packages.close()


# ---------------------------------------------------------------------------
# 3. Real-runtime focused assertions (run under the ``tutor`` interpreter)
# ---------------------------------------------------------------------------


def test_numpy_only_script_succeeds_with_empty_stderr_and_no_artifacts(tmp_path):
    """NumPy-only code must not preload Matplotlib or emit font-cache noise."""
    from tutor.agents.resource.code_sandbox import _safe_run_python
    from tutor.services.config.settings import Settings

    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    status, stdout, stderr, error_code, _deps, artifacts, _duration = _safe_run_python(
        "import numpy as np\nprint(int(np.arange(5).sum()))\n",
        interpreter=sys.executable,
        timeout=60,
        settings=settings,
    )

    assert status == "success"
    assert stdout == "10\n"
    assert stderr == ""
    assert error_code is None
    assert artifacts == []
