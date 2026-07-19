"""Reconcile durable learning evidence into fenced follow-up jobs."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from tutor.core.capability_result import FollowUpTaskSpec
from tutor.services.jobs.follow_up import FollowUpScheduler
from tutor.services.jobs.schema import Job, JobStatus
from tutor.services.jobs.store import JobStore, get_job_store
from tutor.services.learner_profile.schema import LearnerProfile
from tutor.services.learner_profile.store import ProfileStore, get_profile_store
from tutor.services.learning_events.store import (
    LearningEventStore,
    get_learning_event_store,
)

PROFILE_EVENT_THRESHOLD = 5


class LearningWorkflow:
    """Idempotent event→profile scheduling policy."""

    def __init__(
        self,
        *,
        event_store: LearningEventStore | None = None,
        profile_store: ProfileStore | None = None,
        job_store: JobStore | None = None,
    ) -> None:
        self.event_store = event_store or get_learning_event_store()
        self.profile_store = profile_store or get_profile_store()
        self.job_store = job_store or get_job_store()

    @staticmethod
    def root_job_id(user_id: str) -> str:
        digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:40]
        return f"learning-loop-{digest}"

    async def reconcile_user(
        self,
        user_id: str,
        *,
        session_id: str = "",
        through_sequence: int | None = None,
        course: str = "",
    ) -> list[Job]:
        profile = await self.profile_store.get(user_id)
        watermark = profile.event_watermark if profile is not None else 0
        scan_through = (
            int(through_sequence)
            if through_sequence is not None
            else await self.event_store.latest_sequence(user_id)
        )
        through = await self.event_store.profile_trigger_sequence_since(
            user_id,
            watermark,
            through_sequence=scan_through,
            scored_threshold=(1 if profile is None else PROFILE_EVENT_THRESHOLD),
        )
        if through is None:
            if profile is None or await self.profile_store.get_path(
                user_id, profile.version
            ) is not None:
                return []
            return await self._schedule_missing_path(
                profile,
                session_id=session_id,
                course=course,
            )
        window = await self.event_store.list_since(
            user_id,
            watermark,
            through_sequence=through,
        )
        course_window = (
            window
            if scan_through == through
            else await self.event_store.list_since(
                user_id,
                watermark,
                through_sequence=scan_through,
            )
        )
        durable_course = next(
            (event.course for event in reversed(course_window) if event.course),
            course,
        )
        root = await self._ensure_root(user_id, session_id=session_id)
        return await FollowUpScheduler(self.job_store).enqueue(
            root.job_id,
            (
                FollowUpTaskSpec(
                    kind="profile_update",
                    dedupe_key=f"profile_update:{watermark}",
                    payload={
                        "user_id": user_id,
                        "from_watermark": watermark,
                        "through_sequence": through,
                        "course": durable_course,
                    },
                ),
            ),
        )

    async def _ensure_root(self, user_id: str, *, session_id: str) -> Job:
        return await self.job_store.ensure_parent(
            Job(
                job_id=self.root_job_id(user_id),
                user_id=user_id,
                session_id=session_id,
                capability="learning_loop",
                status=JobStatus.SUCCEEDED,
                finished_at=datetime.now(UTC),
                result={"status": "succeeded"},
            )
        )

    async def _schedule_missing_path(
        self,
        profile: LearnerProfile,
        *,
        session_id: str,
        course: str,
    ) -> list[Job]:
        """Create one bounded recovery attempt for a missing current path."""
        root = await self._ensure_root(profile.user_id, session_id=session_id)
        scheduler = FollowUpScheduler(self.job_store)
        payload = {
            "user_id": profile.user_id,
            "profile_version": profile.version,
            "profile": profile.model_dump(mode="json"),
            "course": course,
        }
        original = await scheduler.enqueue(
            root.job_id,
            (
                FollowUpTaskSpec(
                    kind="path_rebuild",
                    dedupe_key=f"path_rebuild:{profile.version}",
                    payload=payload,
                ),
            ),
        )
        if original[0].status in {JobStatus.PENDING, JobStatus.RUNNING}:
            return original
        # Preserve the first terminal attempt for diagnostics. One distinct,
        # deterministic recovery key makes restart repair idempotent and
        # prevents an unbounded retry loop when the underlying issue persists.
        return await scheduler.enqueue(
            root.job_id,
            (
                FollowUpTaskSpec(
                    kind="path_rebuild",
                    dedupe_key=f"path_rebuild:{profile.version}:recovery-1",
                    payload=payload,
                ),
            ),
        )

    async def reconcile_all(self) -> int:
        """Repair durable event→job gaps left by a prior process crash."""
        reconciled = 0
        users = set(await self.event_store.list_users())
        users.update(await self.profile_store.list_users())
        for user_id in sorted(users):
            if await self.reconcile_user(user_id):
                reconciled += 1
        return reconciled


_workflow: LearningWorkflow | None = None


def get_learning_workflow() -> LearningWorkflow:
    global _workflow
    if _workflow is None:
        _workflow = LearningWorkflow()
    return _workflow


def reset_learning_workflow() -> None:
    global _workflow
    _workflow = None


__all__ = [
    "PROFILE_EVENT_THRESHOLD",
    "LearningWorkflow",
    "get_learning_workflow",
    "reset_learning_workflow",
]
