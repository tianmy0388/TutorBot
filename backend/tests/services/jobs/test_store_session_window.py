from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tutor.services.jobs import Job
from tutor.services.jobs.store import JobStore


@pytest.mark.asyncio
async def test_list_for_session_keeps_newest_window_in_chronological_order(
    tmp_path,
) -> None:
    store = JobStore(db_path=tmp_path / "jobs.db")
    await store.init()
    base = datetime(2026, 1, 1, tzinfo=UTC)
    try:
        for index in range(55):
            await store.save(
                Job(
                    job_id=f"job-{index:02d}",
                    user_id="u1",
                    session_id="window-session",
                    capability="resource_generation",
                    message="generate",
                    language="zh",
                    created_at=base + timedelta(minutes=index),
                )
            )

        jobs = await store.list_for_session("window-session", limit=50)

        assert [job["job_id"] for job in jobs] == [
            f"job-{index:02d}" for index in range(5, 55)
        ]
    finally:
        await store.close()
