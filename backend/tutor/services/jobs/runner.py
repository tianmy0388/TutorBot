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
from tutor.services.jobs.contracts import (
    JobError,
    JobResultContract,
    JobTerminalStatus,
)
from tutor.services.jobs.schema import Job, JobStatus, JobSubmit
from tutor.services.jobs.store import JobStore, get_job_store
from pydantic import ValidationError


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
        if job.status in (
            JobStatus.SUCCEEDED,
            JobStatus.PARTIAL,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        ):
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
            JobStatus.SUCCEEDED,
            JobStatus.PARTIAL,
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
        if event_dict.get("type") in ("done", "error", "cancelled", "job_terminal"):
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
          - build a normalized :class:`JobResultContract` at terminal time
          - mark SUCCEEDED / PARTIAL / FAILED / CANCELLED on exit
          - broadcast a single ``job_terminal`` event carrying the contract
        """
        cap = self.capabilities.get(job.capability)
        if cap is None:
            finished_at = datetime.now(timezone.utc)
            contract = JobResultContract(
                job_id=job.job_id,
                capability=job.capability,
                status=JobTerminalStatus.FAILED,
                assistant_message=f"未知能力：{job.capability}",
                error=JobError(
                    code="UNKNOWN_CAPABILITY",
                    message=f"未知能力：{job.capability}",
                    retryable=False,
                ),
                finished_at=finished_at,
            )
            await self.store.update_status(
                job.job_id,
                status=JobStatus.FAILED,
                finished_at=finished_at,
                error=f"unknown capability: {job.capability}",
                result=contract.model_dump(mode="json"),
            )
            await self._broadcast(job.job_id, self._terminal_event(job, contract))
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
        cancelled = False

        # Watchdog: when the capability finishes (cleanly or via exception)
        # close the bus so the subscribe_iter loop unblocks. Without this
        # a capability that forgets to emit ``done`` would hang the job
        # in RUNNING forever.
        async def _watch_and_close() -> None:
            try:
                await run_task
            finally:
                with __import__("contextlib").suppress(Exception):
                    await bus.close()

        watchdog = asyncio.create_task(_watch_and_close())

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
            cancelled = True
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
            # Watchdog task already closes the bus, but if subscribe_iter
            # ended first (capability emitted ``done``) we still want the
            # capability to be awaited here.
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
            # Ensure watchdog is cleaned up.
            if not watchdog.done():
                watchdog.cancel()
                with __import__("contextlib").suppress(Exception):
                    await watchdog

            finished_at = datetime.now(timezone.utc)
            # If a cancel() call already marked this job CANCELLED, keep
            # that status — don't clobber it with FAILED.
            current = await self.store.get(job.job_id)
            if current is not None and current.status == JobStatus.CANCELLED:
                # ensure finished_at is set; status is preserved
                contract = self._build_contract(
                    job,
                    final_result=final_result,
                    error_msg=error_msg,
                    terminal_status=JobTerminalStatus.CANCELLED,
                    finished_at=finished_at,
                )
            else:
                contract = self._build_contract(
                    job,
                    final_result=final_result,
                    error_msg=error_msg,
                    terminal_status=JobTerminalStatus.CANCELLED if cancelled else None,
                    finished_at=finished_at,
                )

            # Persist the terminal event BEFORE the terminal status, so
            # a poll that observes the terminal status also observes the
            # terminal event in the replay buffer. The seq is computed
            # from the current last_seq so resume-replay can deliver
            # terminal events with their full metadata.
            next_seq = await self._next_seq(job.job_id)
            terminal_evt = self._terminal_event(job, contract, seq=next_seq)
            persisted = await self._persist_terminal_once(job.job_id, terminal_evt)
            if not persisted:
                logger.debug(
                    f"JobRunner terminal event already persisted job={job.job_id[:12]}…"
                )

            if current is not None and current.status == JobStatus.CANCELLED:
                await self.store.update_status(
                    job.job_id,
                    status=JobStatus.CANCELLED,
                    finished_at=finished_at,
                    result=contract.model_dump(mode="json"),
                )
            else:
                job_status = _contract_to_job_status(contract.status)
                err_text = (
                    contract.error.diagnostic or contract.error.message
                    if contract.error is not None
                    else None
                )
                await self.store.update_status(
                    job.job_id,
                    status=job_status,
                    finished_at=finished_at,
                    result=contract.model_dump(mode="json"),
                    error=err_text,
                )

            # Always broadcast a single ``job_terminal`` event with the
            # normalized contract. The frontend MUST consume this event
            # (not the legacy empty ``done``) to render the visible
            # assistant message.
            await self._broadcast(job.job_id, terminal_evt)

    # ------------------------------------------------------------------
    # Contract helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_contract(
        job: Job,
        *,
        final_result: dict[str, Any] | None,
        error_msg: str | None,
        terminal_status: JobTerminalStatus | None,
        finished_at: datetime,
    ) -> JobResultContract:
        """Build a :class:`JobResultContract` from the in-flight state.

        Precedence (highest first):
          1. explicit ``terminal_status`` (e.g. ``CANCELLED``)
          2. error event without result  → ``FAILED`` with CAPABILITY_ERROR
          3. no result event at all       → ``FAILED`` with MISSING_RESULT
          4. structured contract inside the result event → use it
          5. mixed succeeded/failed artifacts → ``PARTIAL``
          6. otherwise → ``SUCCEEDED``
        """
        # 1. explicit cancellation wins
        if terminal_status == JobTerminalStatus.CANCELLED:
            return JobResultContract(
                job_id=job.job_id,
                capability=job.capability,
                status=JobTerminalStatus.CANCELLED,
                assistant_message="任务已停止",
                finished_at=finished_at,
            )

        # 2. error event
        if error_msg is not None and final_result is None:
            return JobResultContract(
                job_id=job.job_id,
                capability=job.capability,
                status=JobTerminalStatus.FAILED,
                assistant_message=f"任务失败：{error_msg}"[:200],
                error=JobError(
                    code="CAPABILITY_ERROR",
                    message=error_msg[:200],
                    diagnostic=error_msg,
                    retryable=True,
                ),
                finished_at=finished_at,
            )

        # 3. no result event at all
        if final_result is None:
            return JobResultContract(
                job_id=job.job_id,
                capability=job.capability,
                status=JobTerminalStatus.FAILED,
                assistant_message="任务未返回有效结果",
                error=JobError(
                    code="MISSING_RESULT",
                    message="能力未返回结构化结果",
                    retryable=True,
                ),
                finished_at=finished_at,
            )

        # 4. structured contract inside the result event
        if isinstance(final_result, dict) and isinstance(final_result.get("result_contract"), dict):
            try:
                base = JobResultContract.model_validate(
                    {
                        **final_result["result_contract"],
                        "job_id": job.job_id,
                        "capability": job.capability,
                        "finished_at": finished_at,
                    }
                )
                return base
            except ValidationError:
                # fall through to default
                pass

        # 5 + 6. infer status from artifacts; default SUCCEEDED
        artifacts: list[dict[str, Any]] = []
        if isinstance(final_result, dict):
            raw_artifacts = final_result.get("artifacts")
            if isinstance(raw_artifacts, list):
                artifacts = [a for a in raw_artifacts if isinstance(a, dict)]
        statuses = [a.get("status") for a in artifacts]
        if artifacts and "succeeded" in statuses and "failed" in statuses:
            status = JobTerminalStatus.PARTIAL
            ok = sum(1 for s in statuses if s == "succeeded")
            bad = sum(1 for s in statuses if s == "failed")
            failed_types = [
                a.get("resource_type", "?")
                for a in artifacts
                if a.get("status") == "failed"
            ]
            assistant_message = (
                f"已生成 {ok} 项资源，{bad} 项失败：{', '.join(failed_types)}"
            )
        elif artifacts and statuses and all(s == "failed" for s in statuses):
            status = JobTerminalStatus.FAILED
            assistant_message = "所有资源生成均失败"
        else:
            status = JobTerminalStatus.SUCCEEDED
            assistant_message = (
                (final_result.get("assistant_message") if isinstance(final_result, dict) else None)
                or (final_result.get("summary") if isinstance(final_result, dict) else None)
                or "任务完成"
            )

        return JobResultContract(
            job_id=job.job_id,
            capability=job.capability,
            status=status,
            assistant_message=assistant_message,
            finished_at=finished_at,
        )

    @staticmethod
    def _terminal_event(
        job: Job,
        contract: JobResultContract,
        *,
        seq: int = 0,
    ) -> dict[str, Any]:
        """Build the canonical ``job_terminal`` event.

        ``seq`` should be the next sequence number in the job's
        replay buffer (the runner looks it up before calling).
        Defaults to 0 for backward compatibility, but every caller
        in the runner path now passes the real value.
        """
        return {
            "type": "job_terminal",
            "source": "job_runner",
            "stage": "terminal",
            "content": contract.assistant_message,
            "job_id": job.job_id,
            "session_id": job.session_id,
            "turn_id": "",
            "seq": seq,
            "timestamp": datetime.now(timezone.utc).timestamp(),
            "event_id": uuid.uuid4().hex,
            "metadata": {
                "job_id": job.job_id,
                "session_id": job.session_id,
                "contract": contract.model_dump(mode="json"),
            },
        }

    async def _next_seq(self, job_id: str) -> int:
        """Return the next sequence number for a job's replay buffer."""
        job = await self.store.get(job_id)
        if job is None:
            return 0
        return (job.last_seq or 0) + 1

    async def _persist_terminal_once(
        self,
        job_id: str,
        terminal_evt: dict[str, Any],
    ) -> bool:
        """Persist the terminal event idempotently.

        Returns ``True`` if this call performed the write, ``False``
        if a prior run already did. The guard is a simple
        last-event-type check on the replay buffer — fast, and
        good enough to prevent double-terminal broadcasts when a
        process restarts mid-job and tries to resume.
        """
        job = await self.store.get(job_id)
        if job is None:
            return False
        events = list(job.events or [])
        if events and events[-1].get("type") == "job_terminal":
            return False
        await self.store.append_event(job_id, terminal_evt, terminal_evt["seq"])
        return True

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


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------


def _contract_to_job_status(terminal: JobTerminalStatus) -> JobStatus:
    """Map a :class:`JobTerminalStatus` to the persistent :class:`JobStatus`."""
    return {
        JobTerminalStatus.SUCCEEDED: JobStatus.SUCCEEDED,
        JobTerminalStatus.PARTIAL: JobStatus.PARTIAL,
        JobTerminalStatus.FAILED: JobStatus.FAILED,
        JobTerminalStatus.CANCELLED: JobStatus.CANCELLED,
    }[terminal]