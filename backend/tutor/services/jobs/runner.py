"""JobRunner — async background-task execution engine.

Why a custom runner (not Celery/Arq)?
    The MVP doesn't need distributed task scheduling. A single-process
    asyncio.Task pool backed by the SQLite :class:`JobStore` is enough
    for an interactive educational tool, and avoids Redis as a hard
    dependency. The runner is fully replaceable: ``JobStore`` remains
    the source of truth and the WS layer talks to ``JobRunner`` via
    a stable interface (submit / subscribe / cancel / list).

Per-job lifecycle (driven from :class:`MainOrchestrator`):

    submit(job)
        ↓
    create asyncio.Task → _execute(job)
        ↓
    _execute: create UnifiedContext + new StreamBus + cap.run()
              ├─ subscribe_iter → append_event + broadcast
              └─ final result → update_status(COMPLETED, result=…)
        ↓
    subscribers (WS clients) receive events live

Subscribers connecting mid-run first receive the *replay buffer*
(``Job.events`` in the store) before going live, so reconnection
after a dropped WS is seamless.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from loguru import logger

from tutor.core.context import UnifiedContext
from tutor.core.stream import StreamEvent
from tutor.core.stream_bus import StreamBus
from tutor.runtime.registry.capability_registry import (
    CapabilityRegistry,
    get_capability_registry,
)
from tutor.services.jobs.schema import Job, JobStatus, JobSubmit
from tutor.services.jobs.store import JobStore, get_job_store


class JobRunner:
    """Async background-task runner for capabilities.

    All long-lived state (the asyncio.Task pool + live subscriber
    queues) is kept in process memory. Persistent state lives in the
    SQLite-backed :class:`JobStore`.
    """

    def __init__(
        self,
        *,
        job_store: JobStore | None = None,
        capability_registry: CapabilityRegistry | None = None,
    ) -> None:
        self.store = job_store or get_job_store()
        self.capabilities = capability_registry or get_capability_registry()

        # job_id → asyncio.Task executing _run()
        self._tasks: dict[str, asyncio.Task] = {}
        # job_id → list[asyncio.Queue[dict | None]] of live subscribers
        self._subscribers: dict[str, list[asyncio.Queue[Any]]] = defaultdict(list)
        # Monotonically increasing broadcast id (used for log scoping)
        self._lock = threading.Lock()
        # Capabilities currently executing a job (for /capabilities introspection)
        self._running_user: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Submit / cancel
    # ------------------------------------------------------------------

    async def submit(self, req: JobSubmit) -> Job:
        """Accept a job, persist as PENDING, schedule execution."""
        # Resolve capability (cheap, no LLM — defaults to resource_generation)
        cap_name = req.capability or "resource_generation"
        if self.capabilities.get(cap_name) is None:
            raise ValueError(f"Unknown capability: {cap_name!r}")

        job = Job(
            user_id=req.user_id or "anonymous",
            session_id=req.session_id or uuid.uuid4().hex,
            capability=cap_name,
            message=req.message,
            language=req.language or "zh",
            metadata=dict(req.metadata or {}),
            status=JobStatus.PENDING,
        )
        await self.store.save(job)

        # Schedule execution on the event loop
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._execute(job))
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda t, jid=job.job_id: self._on_task_done(jid, t))
        logger.info(
            f"JobRunner.submit job={job.job_id[:12]}… "
            f"user={job.user_id} capability={job.capability}"
        )
        return job

    async def cancel(self, job_id: str, *, user_id: str | None = None) -> bool:
        """Cancel a PENDING or RUNNING job."""
        job = await self.store.get(job_id)
        if job is None:
            return False
        if user_id is not None and job.user_id != user_id:
            return False
        if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return False

        # Mark cancelling so we don't re-mark it after the task exits.
        await self.store.update_status(
            job_id,
            status=JobStatus.CANCELLED,
            finished_at=datetime.now(timezone.utc),
            error="cancelled by user",
        )
        # Broadcast a cancellation event
        await self._broadcast(
            job_id,
            {
                "type": "cancelled",
                "source": "job_runner",
                "stage": "",
                "content": "Job cancelled by user",
                "metadata": {"job_id": job_id},
                "session_id": job.session_id,
                "turn_id": "",
                "seq": 0,
                "timestamp": datetime.now(timezone.utc).timestamp(),
                "event_id": uuid.uuid4().hex,
            },
        )
        # Cancel the asyncio task
        task = self._tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()
        return True

    def _on_task_done(self, job_id: str, task: asyncio.Task) -> None:
        # Clean up the in-process task reference. Persistent state lives
        # in JobStore.
        self._tasks.pop(job_id, None)
        self._running_user.pop(job_id, None)
        if task.cancelled():
            logger.info(f"JobRunner task cancelled job={job_id[:12]}…")
        elif task.exception() is not None:
            # If the task crashed before we could mark FAILED, do it now.
            logger.warning(
                f"JobRunner task crashed job={job_id[:12]}… exc={task.exception()!r}"
            )
        else:
            logger.info(f"JobRunner task done job={job_id[:12]}…")

    # ------------------------------------------------------------------
    # Subscribe (live event stream for a job)
    # ------------------------------------------------------------------

    async def subscribe(self, job_id: str) -> AsyncIterator[dict[str, Any]]:
        """Yield the job's events: replay-buffer first, then live.

        The iterator exits cleanly when the job terminates (or when the
        caller breaks out).
        """
        job = await self.store.get(job_id)
        if job is None:
            raise KeyError(f"job not found: {job_id}")

        # Replay buffer first (events the caller missed).
        # Snapshot the list to avoid surprises if the store mutates.
        replay = list(job.events or [])
        for evt in replay:
            yield evt

        # If the job already terminated, stop here.
        if job.status in (
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        ):
            return

        # Live: register a queue and yield events until termination.
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=1024)
        self._subscribers[job_id].append(q)
        try:
            while True:
                evt = await q.get()
                if evt is None:
                    return
                yield evt
        finally:
            with self._subs_lock():
                if q in self._subscribers.get(job_id, []):
                    self._subscribers[job_id].remove(q)

    def _subs_lock(self) -> Any:
        # Lightweight lock context manager; we don't want to block event
        # delivery on a heavyweight lock.
        class _Null:
            def __enter__(self_): return self_

            def __exit__(self_, *args): return False

        return _Null()

    async def _broadcast(self, job_id: str, event_dict: dict[str, Any]) -> None:
        queues = list(self._subscribers.get(job_id, []))
        for q in queues:
            try:
                q.put_nowait(event_dict)
            except asyncio.QueueFull:
                logger.warning(
                    f"JobRunner subscriber queue full job={job_id[:12]}… "
                    f"size={q.maxsize}; dropping event"
                )
        # Always close the queues when we emit a terminal sentinel.
        if event_dict.get("type") in ("done", "error", "cancelled"):
            for q in queues:
                with __import__("contextlib").suppress(asyncio.QueueFull):
                    q.put_nowait(None)

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def _execute(self, job: Job) -> None:
        """Run a single job to completion.

        Responsibilities:
          - mark RUNNING with started_at
          - run the capability via MainOrchestrator-equivalent flow
          - tail the bus → append_event + broadcast
          - capture the final ``result`` event
          - mark COMPLETED / FAILED on exit
        """
        cap = self.capabilities.get(job.capability)
        if cap is None:
            await self.store.update_status(
                job.job_id,
                status=JobStatus.FAILED,
                finished_at=datetime.now(timezone.utc),
                error=f"unknown capability: {job.capability}",
            )
            return

        # Mark RUNNING
        await self.store.update_status(
            job.job_id,
            status=JobStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )
        self._running_user[job.job_id] = job.user_id

        # Build the execution context. Reuse the bus from the context so
        # we can subscribe_iter() to it cleanly.
        context = UnifiedContext(
            session_id=job.session_id,
            user_id=job.user_id,
            user_message=job.message,
            language=job.language,
            capability=job.capability,
            metadata=dict(job.metadata or {}),
        )
        bus: StreamBus = context.stream_bus

        run_task = asyncio.create_task(cap.run(context, bus))
        final_result: dict[str, Any] | None = None
        error_msg: str | None = None

        try:
            async for evt in bus.subscribe_iter():
                evt_dict = evt.to_dict()
                await self.store.append_event(job.job_id, evt_dict, evt.seq)
                await self._broadcast(job.job_id, evt_dict)

                if evt.type == "result":
                    # The capability serialised its structured payload as
                    # JSON in evt.content; parse it once and keep.
                    try:
                        final_result = json.loads(evt.content)
                    except (TypeError, ValueError):
                        final_result = {"raw": evt.content}
                elif evt.type == "error" and error_msg is None:
                    error_msg = evt.content
        except asyncio.CancelledError:
            error_msg = "cancelled"
            await self._broadcast(
                job.job_id,
                {
                    "type": "cancelled",
                    "source": "job_runner",
                    "stage": "",
                    "content": "Job cancelled",
                    "metadata": {"job_id": job.job_id},
                    "session_id": job.session_id,
                    "turn_id": "",
                    "seq": 0,
                    "timestamp": datetime.now(timezone.utc).timestamp(),
                    "event_id": uuid.uuid4().hex,
                },
            )
        except Exception as exc:  # noqa: BLE001
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.exception(f"JobRunner._execute crashed job={job.job_id[:12]}…")
        finally:
            # Make sure the capability task is awaited.
            if not run_task.done():
                try:
                    await asyncio.wait_for(run_task, timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"JobRunner capability did not finish promptly job={job.job_id[:12]}…"
                    )
                    run_task.cancel()
                    try:
                        await run_task
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"Capability exited with: {exc!r}")

            finished_at = datetime.now(timezone.utc)
            # If a cancel() call already marked this job CANCELLED, keep
            # that status — don't clobber it with FAILED.
            current = await self.store.get(job.job_id)
            if current is not None and current.status == JobStatus.CANCELLED:
                # ensure finished_at is set; status is preserved
                await self.store.update_status(
                    job.job_id,
                    status=JobStatus.CANCELLED,
                    finished_at=finished_at,
                )
            elif error_msg is not None and final_result is None:
                await self.store.update_status(
                    job.job_id,
                    status=JobStatus.FAILED,
                    finished_at=finished_at,
                    error=error_msg,
                )
            else:
                await self.store.update_status(
                    job.job_id,
                    status=JobStatus.COMPLETED,
                    finished_at=finished_at,
                    result=final_result or {},
                )

            # Make sure subscribers see the terminal sentinel even if
            # the capability forgot to emit ``done``.
            await self._broadcast(
                job.job_id,
                {
                    "type": "done",
                    "source": "job_runner",
                    "stage": "",
                    "content": "",
                    "metadata": {"job_id": job.job_id, "status": "completed"},
                    "session_id": job.session_id,
                    "turn_id": "",
                    "seq": 0,
                    "timestamp": finished_at.timestamp(),
                    "event_id": uuid.uuid4().hex,
                },
            )

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    async def resume_active_jobs(self) -> int:
        """On startup: pick up jobs that were RUNNING when the process died.

        We can't reliably resume an asyncio.Task that's gone, so we mark
        such jobs as FAILED with a clear error. New submissions work fine
        after that.
        """
        active = await self.store.list_active()
        count = 0
        for job in active:
            if job.status == JobStatus.RUNNING:
                # Was running when the process died; mark as failed.
                await self.store.update_status(
                    job.job_id,
                    status=JobStatus.FAILED,
                    finished_at=datetime.now(timezone.utc),
                    error="process restarted while job was running",
                )
                count += 1
            elif job.status == JobStatus.PENDING:
                # Never started; safe to leave as PENDING and let the
                # next submit() flow bring them up via re-submission.
                # For now, mark FAILED too so the UI doesn't hang.
                await self.store.update_status(
                    job.job_id,
                    status=JobStatus.FAILED,
                    finished_at=datetime.now(timezone.utc),
                    error="process restarted before job could start",
                )
                count += 1
        if count:
            logger.warning(f"JobRunner.resume_active_jobs marked {count} orphan jobs as FAILED")
        return count


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


_runner: JobRunner | None = None
_runner_lock = threading.Lock()


def get_job_runner() -> JobRunner:
    global _runner
    if _runner is None:
        with _runner_lock:
            if _runner is None:
                _runner = JobRunner()
                logger.info("JobRunner singleton created")
    return _runner


def reset_job_runner() -> None:
    global _runner
    _runner = None


__all__ = ["JobRunner", "get_job_runner", "reset_job_runner"]