"""Unified WebSocket endpoint — main streaming channel.

Two-phase protocol (Phase 5.2):

    1. Client connects → may submit a job OR subscribe to an existing one.

       Submit:
           C → S: {"type": "submit_job", "user_id": ..., "message": ...,
                    "capability": ..., "language": ...}
           S → C: {"type": "job_submitted", "job_id": "..."}
           (then S closes the WS for the submit leg)

       Subscribe:
           C → S: {"type": "subscribe_job", "job_id": "..."}
           S → C: {"type": "ack", "for": "subscribe_job", "job_id": "..."}
           S → C: <replay buffer of events>
           S → C: <live events>
           S → C: {"type": "done"} | {"type": "error"} | {"type": "cancelled"}
           (server closes the WS)

    2. Legacy protocol (Phase 2 — kept for back-compat):
           C → S: {"type": "start_turn", "session_id": ..., ...}
           S → C: <live events>
           S → C: {"type": "done"}
       Internally, ``start_turn`` now goes through ``JobRunner`` so the
       job is persisted and any disconnect can be resumed via
       ``subscribe_job``.

Other client messages:
    {"type": "ping"}            → {"type": "pong"}
    {"type": "cancel", "job_id": "..."} → {"type": "ack", "for": "cancel"}
"""

from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from tutor.services.identity import IdentityPolicy, identity_policy_for
from tutor.services.jobs import JobSubmit, get_job_runner, get_job_store
from tutor.services.logging import redact_sensitive

router = APIRouter()


@router.websocket("/ws")
async def unified_ws(websocket: WebSocket) -> None:
    """Single WebSocket endpoint handling all client interactions."""
    await websocket.accept()
    identity_policy = identity_policy_for(websocket)
    runner = get_job_runner()

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                envelope = json.loads(raw)
            except json.JSONDecodeError:
                await _send_error(websocket, "Invalid JSON")
                continue

            msg_type = envelope.get("type", "start_turn")

            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if msg_type == "submit_job":
                await _handle_submit(websocket, runner, envelope, identity_policy)
                continue

            if msg_type == "subscribe_job":
                await _handle_subscribe(websocket, runner, envelope, identity_policy)
                continue

            if msg_type == "cancel":
                await _handle_cancel(websocket, runner, envelope, identity_policy)
                continue

            # Legacy: start_turn — internally routed through JobRunner
            if msg_type == "start_turn":
                await _handle_legacy_start_turn(websocket, runner, envelope, identity_policy)
                continue

            await _send_error(websocket, f"Unknown message type: {msg_type!r}")

    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected")
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "UNIFIED_WS_FAILED details={details}",
            details=redact_sensitive(
                {
                    "error_code": "UNIFIED_WS_FAILED",
                    "exception_type": type(exc).__name__,
                }
            ),
        )
        with suppress(Exception):
            await _send_error(websocket, "Server error")


# ---------------------------------------------------------------------------
# submit_job
# ---------------------------------------------------------------------------


async def _handle_submit(
    websocket: WebSocket,
    runner,
    envelope: dict[str, Any],
    identity_policy: IdentityPolicy,
) -> None:
    """Accept a job, persist it, ack with job_id, then close the WS."""
    try:
        req = JobSubmit(
            user_id=identity_policy.resolve(envelope.get("user_id")),
            message=envelope.get("message") or "",
            capability=envelope.get("capability") or None,
            language=envelope.get("language") or "zh",
            session_id=envelope.get("session_id") or None,
            metadata=dict(envelope.get("metadata") or {}),
        )
        job = await runner.submit(req)
    except ValueError as exc:
        await _send_error(websocket, str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "JOB_SUBMIT_FAILED details={details}",
            details=redact_sensitive(
                {
                    "error_code": "JOB_SUBMIT_FAILED",
                    "exception_type": type(exc).__name__,
                }
            ),
        )
        await _send_error(websocket, "submit failed")
        return

    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "job_submitted",
                    "job_id": job.job_id,
                    "user_id": job.user_id,
                    "capability": job.capability,
                    "status": job.status.value,
                    "created_at": job.created_at.isoformat(),
                },
                ensure_ascii=False,
            )
        )
    except Exception:
        # Best-effort: if the client already disconnected, the job is
        # still in the store and they can subscribe later.
        logger.warning(f"submit_job: failed to ack job={job.job_id[:12]}…")


# ---------------------------------------------------------------------------
# subscribe_job (live event stream)
# ---------------------------------------------------------------------------


async def _handle_subscribe(
    websocket: WebSocket,
    runner,
    envelope: dict[str, Any],
    identity_policy: IdentityPolicy,
) -> None:
    """Stream events for a known job until terminal."""
    job_id = envelope.get("job_id")
    if not job_id:
        await _send_error(websocket, "subscribe_job requires job_id")
        return

    try:
        user_id = identity_policy.resolve(envelope.get("user_id"))
    except ValueError as exc:
        await _send_error(websocket, str(exc))
        return

    job = await get_job_store().get(job_id)
    if job is None or job.user_id != user_id:
        await _send_error(websocket, "job not found")
        return

    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "ack",
                    "for": "subscribe_job",
                    "job_id": job_id,
                },
                ensure_ascii=False,
            )
        )
    except Exception:
        return

    try:
        async for evt in runner.subscribe(job_id):
            try:
                await websocket.send_text(json.dumps(evt, ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "JOB_SUBSCRIPTION_SEND_FAILED details={details}",
                    details=redact_sensitive(
                        {
                            "error_code": "JOB_SUBSCRIPTION_SEND_FAILED",
                            "job_id": job_id[:12],
                            "exception_type": type(exc).__name__,
                        }
                    ),
                )
                return
    except KeyError as exc:
        await _send_error(websocket, str(exc))
        return


# ---------------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------------


async def _handle_cancel(
    websocket: WebSocket,
    runner,
    envelope: dict[str, Any],
    identity_policy: IdentityPolicy,
) -> None:
    job_id = envelope.get("job_id") or ""
    if not job_id:
        await _send_error(websocket, "cancel requires job_id")
        return
    try:
        user_id = identity_policy.resolve(envelope.get("user_id"))
    except ValueError as exc:
        await _send_error(websocket, str(exc))
        return
    ok = await runner.cancel(job_id, user_id=user_id)
    with suppress(Exception):
        await websocket.send_text(
            json.dumps(
                {"type": "ack", "for": "cancel", "job_id": job_id, "cancelled": ok},
                ensure_ascii=False,
            )
        )


# ---------------------------------------------------------------------------
# Legacy start_turn (Phase 2 compat) — now goes through JobRunner
# ---------------------------------------------------------------------------


async def _handle_legacy_start_turn(
    websocket: WebSocket,
    runner,
    envelope: dict[str, Any],
    identity_policy: IdentityPolicy,
) -> None:
    """Back-compat: accept a ``start_turn`` envelope, run it as a job,
    and stream events back over the same WS until the job terminates.

    The job is persisted just like a normal submit, so the client can
    later reconnect and ``subscribe_job`` to recover.
    """
    user_message = envelope.get("message") or ""
    history = envelope.get("history") or []
    language = envelope.get("language") or "zh"
    capability = envelope.get("capability") or None
    session_id = envelope.get("session_id") or ""

    try:
        req = JobSubmit(
            user_id=identity_policy.resolve(envelope.get("user_id")),
            message=user_message,
            capability=capability,
            language=language,
            session_id=session_id,
            metadata={"history_count": len(history), "legacy": True},
        )
        job = await runner.submit(req)
    except ValueError as exc:
        await _send_error(websocket, str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        await _send_error(websocket, f"submit failed: {exc}")
        return

    # Tell the client the job_id so they can resume via subscribe_job
    # if this WS drops.
    try:
        await websocket.send_text(
            json.dumps(
                {
                    "type": "job_submitted",
                    "job_id": job.job_id,
                    "user_id": job.user_id,
                    "capability": job.capability,
                    "legacy": True,
                },
                ensure_ascii=False,
            )
        )
    except Exception:
        return

    try:
        async for evt in runner.subscribe(job.job_id):
            try:
                await websocket.send_text(json.dumps(evt, ensure_ascii=False))
            except Exception:
                return
    except KeyError as exc:
        await _send_error(websocket, str(exc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _send_error(websocket: WebSocket, message: str) -> None:
    with suppress(Exception):
        await websocket.send_text(json.dumps({"type": "error", "content": message}, ensure_ascii=False))


__all__ = ["router"]
