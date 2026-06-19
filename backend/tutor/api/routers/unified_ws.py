"""Unified WebSocket endpoint — main streaming channel.

The client connects, sends a JSON envelope describing the user turn, and
receives a stream of :class:`StreamEvent` JSON frames.

Client → server messages
------------------------

    {"type": "start_turn", "session_id": "...", "user_id": "...", "message": "..."}
    {"type": "cancel", "turn_id": "..."}
    {"type": "ping"}

Server → client messages
------------------------

    StreamEvent JSON objects (see :class:`StreamEvent`):
        {"type": "stage_start", "stage": "...", "source": "...", ...}
        {"type": "content", "content": "chunk", ...}
        {"type": "done", ...}
        {"type": "error", ...}

Design inspired by DeepTutor's ``deeptutor/api/routers/unified_ws.py``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from loguru import logger

from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.runtime import get_orchestrator

router = APIRouter()


@router.websocket("/ws")
async def unified_ws(websocket: WebSocket) -> None:
    """Single WebSocket endpoint handling all client interactions."""
    await websocket.accept()
    orchestrator = get_orchestrator()

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

            if msg_type == "start_turn":
                await _handle_turn(websocket, orchestrator, envelope)
                continue

            if msg_type == "cancel":
                # MVP: cancellation is a no-op (the orchestrator doesn't
                # expose a cancellation token yet — Phase 2).
                await websocket.send_text(
                    json.dumps({"type": "ack", "for": "cancel"})
                )
                continue

            await _send_error(websocket, f"Unknown message type: {msg_type!r}")

    except WebSocketDisconnect:
        logger.debug("WebSocket disconnected")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"Unified WS error: {exc!r}")
        try:
            await _send_error(websocket, f"Server error: {exc}")
        except Exception:
            pass


async def _handle_turn(
    websocket: WebSocket,
    orchestrator,
    envelope: dict[str, Any],
) -> None:
    """Process one user turn and stream events back to the client."""
    session_id = envelope.get("session_id") or ""
    user_id = envelope.get("user_id") or "anonymous"
    user_message = envelope.get("message") or ""
    history = envelope.get("history") or []
    language = envelope.get("language") or "zh"

    context = UnifiedContext(
        session_id=session_id,
        user_id=user_id,
        user_message=user_message,
        history=history,
        language=language,
        metadata=dict(envelope.get("metadata") or {}),
    )

    bus = context.stream_bus  # creates a new bus tied to this turn

    async def pump() -> None:
        async for event in orchestrator.handle(context):
            try:
                await websocket.send_text(json.dumps(event.to_dict(), ensure_ascii=False))
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"WebSocket send failed: {exc!r}")
                return

    try:
        await asyncio.wait_for(pump(), timeout=600)  # 10-minute cap
    except asyncio.TimeoutError:
        await bus.error("Turn timed out", source="ws")
        await bus.done(source="ws")
        try:
            await websocket.send_text(json.dumps({"type": "error", "content": "timeout"}))
        except Exception:
            pass


async def _send_error(websocket: WebSocket, message: str) -> None:
    try:
        await websocket.send_text(
            json.dumps({"type": "error", "content": message})
        )
    except Exception:
        pass


__all__ = ["router"]
