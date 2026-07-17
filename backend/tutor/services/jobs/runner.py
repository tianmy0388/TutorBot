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
import traceback
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import ValidationError

from tutor.core.capability_result import CapabilityResult
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
from tutor.services.resource_package.schema import ArtifactRef


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
        # Only the canonical terminal closes live queues.  ``error`` and
        # ``done`` are runner-authored compatibility events that precede it.
        if event_dict.get("type") == "job_terminal":
            for q in queues:
                with __import__("contextlib").suppress(asyncio.QueueFull):
                    q.put_nowait(None)

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def _execute(self, job: Job) -> None:
        """Compatibility wrapper for the single-owner execution path."""
        await self._run_job(job)

    async def _run_job(self, job: Job) -> None:
        """Run one capability and commit exactly one terminal transition."""
        partial_resources: list[dict[str, Any]] = []
        legacy_payload: dict[str, Any] | None = None
        legacy_error: str | None = None
        capability_result: CapabilityResult | None = None
        failure: BaseException | None = None
        failure_traceback: str | None = None
        timeout_exceeded = False

        cap = self.capabilities.get(job.capability)
        await self.store.update_status(
            job.job_id,
            status=JobStatus.RUNNING,
            started_at=datetime.now(UTC),
        )
        self._running_user[job.job_id] = job.user_id

        if cap is None:
            failure = LookupError(f"unknown capability: {job.capability}")
            failure_traceback = "".join(
                traceback.format_exception_only(type(failure), failure)
            )
            await self._finish_job(
                job,
                result=None,
                failure=failure,
                failure_traceback=failure_traceback,
                partial_resources=partial_resources,
            )
            return

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

        # Register before starting the capability.  StreamBus.close appends its
        # sentinel after buffered events, so draining this queue preserves all
        # progress/resource events emitted immediately before completion.
        event_queue = bus.subscribe()
        run_task = asyncio.create_task(cap.run(context, bus))

        try:
            from tutor.services.config.settings import get_settings

            timeout_seconds = int(get_settings().job_timeout_seconds or 0)
        except Exception:
            timeout_seconds = 0

        async def _await_capability() -> None:
            nonlocal capability_result, failure, failure_traceback, timeout_exceeded
            try:
                returned = (
                    await asyncio.wait_for(run_task, timeout=timeout_seconds)
                    if timeout_seconds > 0
                    else await run_task
                )
                if isinstance(returned, CapabilityResult):
                    capability_result = returned
            except TimeoutError as exc:
                timeout_exceeded = True
                failure = exc
                failure_traceback = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
                run_task.cancel()
                with suppress(asyncio.CancelledError):
                    await run_task
            except BaseException as exc:  # noqa: BLE001
                failure = exc
                failure_traceback = "".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )
                logger.error(
                    "JobRunner capability failed job={job_id}: {kind}: {message}",
                    job_id=job.job_id[:12],
                    kind=type(exc).__name__,
                    message=exc,
                )
            finally:
                await bus.close()

        waiter = asyncio.create_task(_await_capability())
        try:
            while True:
                evt = await event_queue.get()
                if evt is None:
                    break
                event_type = getattr(evt.type, "value", str(evt.type))
                if event_type == "result":
                    try:
                        parsed = json.loads(evt.content)
                        legacy_payload = parsed if isinstance(parsed, dict) else {"raw": parsed}
                    except (TypeError, ValueError):
                        legacy_payload = {"raw": evt.content}
                    continue
                if event_type == "error":
                    legacy_error = legacy_error or evt.content
                    continue
                if event_type in {"done", "cancelled", "job_terminal"}:
                    continue

                evt_dict = evt.to_dict()
                await self.store.append_event(job.job_id, evt_dict, evt.seq)
                await self._broadcast(job.job_id, evt_dict)
                if event_type == "resource":
                    md = evt.metadata or {}
                    partial_resources.append(
                        {
                            "resource_type": str(md.get("resource_type") or "unknown"),
                            "status": "succeeded",
                            "resource_id": md.get("resource_id"),
                            "title": md.get("title"),
                            "metadata": {"source_event_seq": evt.seq},
                        }
                    )
        except asyncio.CancelledError as exc:
            failure = exc
            failure_traceback = "Job cancelled"
            run_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await run_task
        finally:
            with suppress(asyncio.CancelledError):
                await waiter

        # Compatibility input only: old capabilities may have emitted a
        # result/error.  They are never persisted or published as authored by
        # the capability; the runner converts them to its internal contract.
        if capability_result is None and legacy_payload is not None:
            capability_result = CapabilityResult(
                assistant_message=(
                    legacy_payload.get("assistant_message")
                    or legacy_payload.get("summary")
                ),
                payload=legacy_payload,
            )
        if failure is None and legacy_error is not None and capability_result is None:
            failure = RuntimeError(legacy_error)
            failure_traceback = legacy_error
        if timeout_exceeded:
            failure = TimeoutError(f"Job timed out after {timeout_seconds}s")
            failure_traceback = failure_traceback or str(failure)

        await asyncio.shield(
            self._finish_job(
                job,
                result=capability_result,
                failure=failure,
                failure_traceback=failure_traceback,
                partial_resources=partial_resources,
            )
        )

    async def _finish_job(
        self,
        job: Job,
        *,
        result: CapabilityResult | None,
        failure: BaseException | None,
        failure_traceback: str | None,
        partial_resources: list[dict[str, Any]],
    ) -> None:
        finished_at = datetime.now(UTC)
        current = await self.store.get(job.job_id)
        cancelled = current is not None and current.status == JobStatus.CANCELLED
        contract = self._build_contract(
            job,
            capability_result=result,
            error_msg=(
                f"{type(failure).__name__}: {failure}" if failure is not None else None
            ),
            error_diagnostic=failure_traceback,
            terminal_status=(
                JobTerminalStatus.CANCELLED if cancelled else None
            ),
            finished_at=finished_at,
            partial_artifacts=partial_resources,
        )
        error_log_ref = None
        if failure is not None:
            error_log_ref = self._write_error_log(
                job.job_id,
                failure_traceback or f"{type(failure).__name__}: {failure}",
            )
        await self._write_terminal(
            job,
            contract=contract,
            capability_result=result,
            finished_at=finished_at,
            error_log_ref=error_log_ref,
        )

    @staticmethod
    def _write_error_log(job_id: str, text: str) -> ArtifactRef:
        from tutor.services.artifacts import to_artifact_key
        from tutor.services.config.settings import get_settings

        data_dir = Path(get_settings().data_dir)
        log_path = data_dir / "job_logs" / job_id / "error.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(text, encoding="utf-8", errors="replace")
        return ArtifactRef(
            name="error.log",
            kind="text",
            artifact_key=to_artifact_key(log_path, data_dir),
        )

    # ------------------------------------------------------------------
    # Contract helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_contract(
        job: Job,
        *,
        capability_result: CapabilityResult | None,
        error_msg: str | None,
        error_diagnostic: str | None,
        terminal_status: JobTerminalStatus | None,
        finished_at: datetime,
        partial_artifacts: list[dict[str, Any]] | None = None,
    ) -> JobResultContract:
        """Convert the internal capability hand-off to the public contract."""
        if terminal_status == JobTerminalStatus.CANCELLED:
            return JobResultContract(
                job_id=job.job_id,
                capability=job.capability,
                status=JobTerminalStatus.CANCELLED,
                assistant_message="任务已停止",
                finished_at=finished_at,
                partial_artifacts=_materialize_partial_artifacts(partial_artifacts),
            )

        if error_msg is not None:
            code = "JOB_TIMEOUT" if error_msg.startswith("TimeoutError:") else "CAPABILITY_FAILED"
            if error_msg.startswith("LookupError: unknown capability"):
                code = "UNKNOWN_CAPABILITY"
            return JobResultContract(
                job_id=job.job_id,
                capability=job.capability,
                status=JobTerminalStatus.FAILED,
                assistant_message=f"任务失败：{error_msg}"[:200],
                error=JobError(
                    code=code,
                    message=error_msg[:200],
                    diagnostic=error_diagnostic or error_msg,
                    retryable=code != "UNKNOWN_CAPABILITY",
                ),
                finished_at=finished_at,
                partial_artifacts=_materialize_partial_artifacts(partial_artifacts),
            )

        if capability_result is None:
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

        payload = capability_result.payload
        if isinstance(payload.get("result_contract"), dict):
            try:
                base = JobResultContract.model_validate(
                    {
                        **payload["result_contract"],
                        "job_id": job.job_id,
                        "capability": job.capability,
                        "finished_at": finished_at,
                    }
                )
                return base
            except ValidationError:
                # fall through to default
                pass

        artifacts: list[dict[str, Any]] = []
        raw_artifacts = payload.get("artifacts")
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
            payload_message = payload.get("assistant_message") or payload.get("summary")
            assistant_message = capability_result.assistant_message or (
                payload_message if isinstance(payload_message, str) else "任务完成"
            )

        public_artifacts: list[ArtifactResult] = []
        for artifact in artifacts:
            with suppress(ValidationError):
                public_artifacts.append(ArtifactResult.model_validate(artifact))
        public_artifacts.extend(
            ArtifactResult(
                resource_type=artifact.kind or "artifact",
                title=artifact.name,
                metadata={"artifact_key": artifact.artifact_key},
            )
            for artifact in capability_result.artifacts
        )

        return JobResultContract(
            job_id=job.job_id,
            capability=job.capability,
            status=status,
            assistant_message=assistant_message,
            artifacts=public_artifacts,
            finished_at=finished_at,
            partial_artifacts=_materialize_partial_artifacts(partial_artifacts),
        )

    async def _write_terminal(
        self,
        job: Job,
        *,
        contract: JobResultContract,
        capability_result: CapabilityResult | None,
        finished_at: datetime,
        error_log_ref: ArtifactRef | None,
    ) -> None:
        """Persist and publish only runner-authored terminal lifecycle events."""
        current = await self.store.get(job.job_id)
        if current is None or any(
            event.get("type") == "job_terminal" for event in (current.events or [])
        ):
            return

        next_seq = (current.last_seq or 0) + 1
        compatibility_events: list[dict[str, Any]] = []
        if contract.status in {JobTerminalStatus.SUCCEEDED, JobTerminalStatus.PARTIAL}:
            result_payload = capability_result.payload if capability_result else {}
            result_event = self._runner_event(
                job,
                event_type="result",
                content=json.dumps(result_payload, ensure_ascii=False, default=str),
                seq=next_seq,
                metadata={
                    "artifacts": [
                        artifact.model_dump(mode="json")
                        for artifact in (capability_result.artifacts if capability_result else ())
                    ],
                    "follow_up_tasks": [
                        {
                            "kind": spec.kind,
                            "payload": spec.payload,
                            "dedupe_key": spec.dedupe_key,
                        }
                        for spec in (
                            capability_result.follow_up_tasks if capability_result else ()
                        )
                    ],
                },
            )
            compatibility_events.append(result_event)
            next_seq += 1
            compatibility_events.append(
                self._runner_event(job, event_type="done", content="", seq=next_seq)
            )
            next_seq += 1
        elif contract.status == JobTerminalStatus.FAILED:
            compatibility_events.append(
                self._runner_event(
                    job,
                    event_type="error",
                    content=contract.error.message if contract.error else contract.assistant_message,
                    seq=next_seq,
                    metadata={
                        "code": contract.error.code if contract.error else "CAPABILITY_FAILED",
                        "error_log_ref": (
                            error_log_ref.model_dump(mode="json")
                            if error_log_ref is not None
                            else None
                        ),
                    },
                )
            )
            next_seq += 1

        for event in compatibility_events:
            await self.store.append_event(job.job_id, event, event["seq"])

        terminal_evt = self._terminal_event(job, contract, seq=next_seq)
        job_status = _contract_to_job_status(contract.status)
        err_text = (
            contract.error.diagnostic or contract.error.message
            if contract.error is not None
            else None
        )
        persisted = await self.store.set_terminal(
            job.job_id,
            status=job_status,
            finished_at=finished_at,
            result=contract.model_dump(mode="json"),
            error=err_text,
            error_log_ref=error_log_ref,
            terminal_event=terminal_evt,
        )
        if not persisted:
            logger.debug(
                "JobRunner terminal event already persisted job={job_id}",
                job_id=job.job_id[:12],
            )

            return
        for event in compatibility_events:
            await self._broadcast(job.job_id, event)
        await self._broadcast(job.job_id, terminal_evt)

    @staticmethod
    def _runner_event(
        job: Job,
        *,
        event_type: str,
        content: str,
        seq: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": event_type,
            "source": "job_runner",
            "stage": "terminal",
            "content": content,
            "job_id": job.job_id,
            "session_id": job.session_id,
            "turn_id": "",
            "seq": seq,
            "timestamp": datetime.now(UTC).timestamp(),
            "event_id": uuid.uuid4().hex,
            "metadata": metadata or {},
        }

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
            elif job.status == JobStatus.PENDING:
                error_msg = "process restarted before job could start"
            else:
                continue
            # Pull every resource event from the replay buffer so the
            # right pane can list "what we got before dying".
            partial_artifacts: list[dict[str, Any]] = []
            for ev in job.events or []:
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
            finished_at = datetime.now(UTC)
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
                finished_at=finished_at,
                partial_artifacts=partial_artifacts,
            )
            await self._write_terminal(
                job,
                contract=contract,
                capability_result=None,
                finished_at=finished_at,
                error_log_ref=self._write_error_log(job.job_id, error_msg),
            )
            count += 1
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
