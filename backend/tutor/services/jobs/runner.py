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
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from loguru import logger
from pydantic import ValidationError

from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.runtime.registry.capability_registry import (
    CapabilityRegistry,
    get_capability_registry,
)
from tutor.services.jobs.contracts import (
    ArtifactResult,
    JobError,
    JobResultContract,
    JobTerminalStatus,
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
            finished_at=datetime.now(UTC),
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
                "timestamp": datetime.now(UTC).timestamp(),
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
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

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
        # **2026-07-08 fix (187b2955):** collect every ``RESOURCE`` event
        # the capability emits so we can attach them as
        # ``partial_artifacts`` on the terminal contract. Without this,
        # a timeout mid-video-render would leave the user staring at an
        # empty right pane even though 3+ resources had already streamed
        # to the trace.
        partial_resources: list[dict[str, Any]] = []

        cap = self.capabilities.get(job.capability)
        if cap is None:
            finished_at = datetime.now(UTC)
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
            started_at=datetime.now(UTC),
        )
        self._running_user[job.job_id] = job.user_id

        # Build the execution context. Reuse the bus from the context so
        # we can subscribe_iter() to it cleanly.
        context = UnifiedContext(
            session_id=job.session_id,
            job_id=job.job_id,
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
        timeout_exceeded = False

        # 2026-06-21 plan (B3): per-job max-runtime timeout.
        # The pre-fix code had no upper bound on job execution.
        # A capability that loops forever (e.g. an LLM call that
        # hangs) would keep the job in RUNNING indefinitely.
        # The timeout is configurable via ``job_timeout_seconds``;
        # 0 means "unlimited". Read from ``get_settings()`` rather
        # than an instance attribute to avoid AttributeError.
        try:
            from tutor.services.config.settings import get_settings
            timeout_seconds = int(get_settings().job_timeout_seconds or 0)
        except Exception:
            timeout_seconds = 0

        # Watchdog: when the capability finishes (cleanly or via
        # exception) OR the timeout fires, close the bus so the
        # subscribe_iter loop unblocks. Without this a capability
        # that forgets to emit ``done`` would hang the job in
        # RUNNING forever.
        #
        # **2026-07-07 fix:** the watchdog now also captures any
        # exception raised by ``run_task`` and writes it to a shared
        # slot (``capability_exc``) so the main ``_execute`` task can
        # surface it as ``error_msg`` in the contract. Before this
        # fix, only ``asyncio.TimeoutError`` was caught here — every
        # other exception escaped the watchdog task and was silently
        # dropped by asyncio ("Task exception was never retrieved"),
        # leaving the contract stuck on ``MISSING_RESULT`` ("能力未
        # 返回结构化结果") with no hint of what actually blew up.
        capability_exc: list[BaseException] = []

        async def _watch_and_close() -> None:
            try:
                if timeout_seconds > 0:
                    await asyncio.wait_for(run_task, timeout=timeout_seconds)
                else:
                    await run_task
            except TimeoutError:
                nonlocal timeout_exceeded
                timeout_exceeded = True
                run_task.cancel()
                with __import__("contextlib").suppress(asyncio.CancelledError):
                    await run_task
            except BaseException as exc:  # noqa: BLE001
                # Capture the real failure so the main task can
                # report it instead of falling through to MISSING_RESULT.
                capability_exc.append(exc)
                logger.exception(
                    "JobRunner capability raised unhandled exception "
                    "job={job_id}: {err}",
                    job_id=job.job_id[:12],
                    err=exc,
                )
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
                elif evt.type == "resource":
                    # **2026-07-08 fix (187b2955):** incremental resource
                    # events from the capability. We materialise a minimal
                    # ``ArtifactResult``-compatible dict so the contract
                    # can surface them even if the pipeline ends without
                    # a final ``result`` event.
                    md = evt.metadata or {}
                    partial_resources.append(
                        {
                            "resource_type": str(
                                md.get("resource_type") or "unknown"
                            ),
                            "status": "succeeded",
                            "resource_id": md.get("resource_id"),
                            "title": md.get("title"),
                            "metadata": {"source_event_seq": evt.seq},
                        }
                    )
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
                    "timestamp": datetime.now(UTC).timestamp(),
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
                except TimeoutError:
                    logger.warning(
                        f"JobRunner capability did not finish promptly job={job.job_id[:12]}…"
                    )
                    run_task.cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await run_task
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"Capability exited with: {exc!r}")
            # Ensure watchdog is cleaned up.
            # **2026-06-22 fix (Task 1):** ``asyncio.CancelledError`` is
            # NOT a subclass of ``Exception`` in Python 3.11 — it lives
            # under ``BaseException``. The pre-fix ``suppress(Exception)``
            # missed it, so ``await watchdog`` after ``.cancel()`` raised
            # ``CancelledError`` straight through the finally block,
            # silently skipping terminal persistence. The job stayed
            # ``running`` forever even though the capability had already
            # emitted ``done`` and ``result``. We now explicitly catch
            # ``CancelledError`` so the terminal block always runs.
            if not watchdog.done():
                watchdog.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await watchdog

            finished_at = datetime.now(UTC)
            # 2026-06-21 plan (B3): when the global timeout fires,
            # surface a distinct error code so the UI can show
            # "任务超时" rather than a generic "failed".
            if timeout_exceeded and error_msg is None:
                error_msg = (
                    f"Job timed out after {timeout_seconds}s "
                    f"(TUTOR_JOB_TIMEOUT_SECONDS)"
                )
            # **2026-07-07 fix:** surface the capability's actual
            # exception (captured by the watchdog) so the contract
            # reports ``CAPABILITY_ERROR`` with a real diagnostic
            # instead of the misleading ``MISSING_RESULT``.
            if error_msg is None and capability_exc:
                exc = capability_exc[0]
                error_msg = f"{type(exc).__name__}: {exc}"
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
                    partial_artifacts=partial_resources,
                )
            else:
                contract = self._build_contract(
                    job,
                    final_result=final_result,
                    error_msg=error_msg,
                    terminal_status=JobTerminalStatus.CANCELLED if cancelled else None,
                    finished_at=finished_at,
                    partial_artifacts=partial_resources,
                )

            # **2026-06-22 fix (Task 1):** the entire terminal
            # persistence block is now shielded from task cancellation.
            # After the ``watchdog.cancel()`` + ``CancelledError`` fix
            # above, the outer task may still be in a canceled state.
            # ``asyncio.shield`` ensures the terminal status / event /
            # broadcast always complete, even if the enclosing task is
            # being torn down. Without this, a fast-completing capability
            # whose ``done`` event closes the bus can cause the
            # cancellation to propagate and skip persistence, leaving
            # the job ``running`` in the database forever.
            try:
                await asyncio.shield(
                    self._write_terminal(
                        job, current,
                        contract=contract,
                        finished_at=finished_at,
                        cancelled=cancelled,
                    )
                )
            except (asyncio.CancelledError, Exception) as exc:  # noqa: BLE001
                logger.exception(
                    "JobRunner terminal-shield failed job={job_id}: {err}",
                    job_id=job.job_id[:12],
                    err=exc,
                )
                # Emergency write so the DB never stays RUNNING forever.
                with suppress(Exception):
                    await self.store.update_status(
                        job.job_id,
                        status=JobStatus.FAILED,
                        finished_at=datetime.now(UTC),
                        error=f"terminal-shield raised: {exc!r}",
                    )

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
        partial_artifacts: list[dict[str, Any]] | None = None,
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
                partial_artifacts=_materialize_partial_artifacts(partial_artifacts),
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
                partial_artifacts=_materialize_partial_artifacts(partial_artifacts),
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
                partial_artifacts=_materialize_partial_artifacts(partial_artifacts),
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
            partial_artifacts=_materialize_partial_artifacts(partial_artifacts),
        )

    async def _write_terminal(
        self,
        job: Job,
        current: Job | None,
        *,
        contract: JobResultContract,
        finished_at: datetime,
        cancelled: bool,
    ) -> None:
        """Idempotent terminal write: event + status + broadcast.

        Called inside ``asyncio.shield`` so cancellation of the
        enclosing task cannot skip persistence.
        """
        next_seq = await self._next_seq(job.job_id)
        terminal_evt = self._terminal_event(job, contract, seq=next_seq)
        persisted = await self._persist_terminal_once(job.job_id, terminal_evt)
        if not persisted:
            logger.debug(
                "JobRunner terminal event already persisted job={job_id}",
                job_id=job.job_id[:12],
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

        await self._broadcast(job.job_id, terminal_evt)

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
            "timestamp": datetime.now(UTC).timestamp(),
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

        **2026-07-09 fix (sess_ebb / 38a445a1 trace):** the reaped
        jobs now also receive a synthesised ``job_terminal`` event.
        Pre-fix, the reaper only flipped the DB row to FAILED — the
        job_terminal WS event was never broadcast (the bus was closed
        when the previous process died). That left the frontend's
        ``event-handler.ts:job_terminal`` branch un-fired, so the
        chat panel never got a workflow timeline / assistant
        summary. Users who switched to the reap'd conversation saw
        a bare "正在调用 Agent…" because the in-memory state
        believed the job was still running. The synthesised
        terminal carries a real ``JobResultContract`` with a
        user-readable assistant_message ("任务未完成: process
        restarted…") and the partial_artifacts list (the
        incremental ``RESOURCE`` events the previous process
        emitted before dying) so the right pane can still show
        what was generated.
        """
        active = await self.store.list_active()
        count = 0
        for job in active:
            if job.status == JobStatus.RUNNING:
                error_msg = "process restarted while job was running"
                # Was running when the process died; mark as failed.
                await self.store.update_status(
                    job.job_id,
                    status=JobStatus.FAILED,
                    finished_at=datetime.now(UTC),
                    error=error_msg,
                )
                count += 1
            elif job.status == JobStatus.PENDING:
                error_msg = "process restarted before job could start"
                # Never started; safe to leave as PENDING and let the
                # next submit() flow bring them up via re-submission.
                # For now, mark FAILED too so the UI doesn't hang.
                await self.store.update_status(
                    job.job_id,
                    status=JobStatus.FAILED,
                    finished_at=datetime.now(UTC),
                    error=error_msg,
                )
                count += 1
            else:
                continue
            # **2026-07-09 fix:** synthesise a terminal contract + event
            # so the frontend's event-handler can save the workflow
            # timeline and assistant message to the conversation.
            # Re-fetch the freshly reaped job (it now has finished_at
            # and a status row we can stuff into the contract).
            reaped = await self.store.get(job.job_id)
            if reaped is None:
                continue
            # Pull every resource event from the replay buffer so the
            # right pane can list "what we got before dying".
            partial_artifacts: list[dict[str, Any]] = []
            for ev in reaped.events or []:
                if ev.get("type") == "resource":
                    md = ev.get("metadata") or {}
                    partial_artifacts.append(
                        {
                            "resource_type": str(
                                md.get("resource_type") or "unknown"
                            ),
                            "status": "succeeded",
                            "resource_id": md.get("resource_id"),
                            "title": md.get("title"),
                            "metadata": {
                                "source_event_seq": ev.get("seq"),
                                "interrupted": True,
                            },
                        }
                    )
            contract = JobResultContract(
                job_id=job.job_id,
                capability=job.capability,
                status=JobTerminalStatus.FAILED,
                assistant_message=(
                    f"任务未完成（{error_msg}）"
                ),
                error=JobError(
                    code="PROCESS_RESTART",
                    message=error_msg,
                    retryable=True,
                ),
                finished_at=datetime.now(UTC),
                partial_artifacts=partial_artifacts,
            )
            seq = await self._next_seq(job.job_id)
            terminal_evt = self._terminal_event(reaped, contract, seq=seq)
            await self._persist_terminal_once(job.job_id, terminal_evt)
            await self._broadcast(job.job_id, terminal_evt)
        if count:
            # This is normal on dev restart — the previous process's
            # asyncio tasks are gone and the UI needs terminal states.
            # Log at INFO, not WARNING, so it doesn't alarm operators.
            logger.info(
                "JobRunner.resume_active_jobs: marked {count} orphan "
                "jobs from previous process as FAILED + synthesised "
                "job_terminal so the frontend can save workflow "
                "timeline messages",
                count=count,
            )
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


def _materialize_partial_artifacts(
    raw: list[dict[str, Any]] | None,
) -> list[ArtifactResult]:
    """Validate + serialise raw resource-event dicts into ``ArtifactResult``.

    **2026-07-08 fix (187b2955):** the runner collects ``RESOURCE`` events
    during ``_execute`` and needs to hand them to ``JobResultContract`` at
    terminal time. The contract is strict (``extra="forbid"``), so any
    malformed dict would 500. We swallow individual validation failures
    here so one bad event doesn't poison the whole partial set.

    **2026-07-08 fix (039b4a70 trace):** dedup by ``resource_id`` so
    a resource that fires ``RESOURCE`` twice (e.g. once from
    ``manim_video``'s inline emit at agent-return time, then again
    from ``_generate_parallel``'s ``as_completed`` yield) does not
    produce duplicate entries in ``contract.partial_artifacts``.
    The frontend used to iterate the duplicates and push the same
    resource into ``latestPackage.resources`` twice, triggering
    React's "Encountered two children with the same key" error.
    Entries without a ``resource_id`` (legacy / malformed events)
    are kept verbatim — they can't conflict with anything.
    """
    if not raw:
        return []
    out: list[ArtifactResult] = []
    seen_ids: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        rid = entry.get("resource_id")
        if isinstance(rid, str) and rid:
            if rid in seen_ids:
                # Already materialised an entry for this resource_id —
                # skip the duplicate. The frontend dedups by
                # resource_id, so a 2nd occurrence carries no new info.
                logger.debug(
                    f"dedup: skipping duplicate partial artifact for "
                    f"resource_id={rid}"
                )
                continue
            seen_ids.add(rid)
        try:
            out.append(ArtifactResult.model_validate(entry))
        except ValidationError:
            logger.debug(
                f"Skipping malformed partial artifact: {entry!r}"
            )
    return out
