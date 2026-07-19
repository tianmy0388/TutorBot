"""Dialogue-driven profile ingestion (conversational profile building).

Post-answer, best-effort companion of the answering capabilities: when the
student's message carries profile signal (see ``signal_detector``), run the
LLM feature extractor, merge the diff, emit a visible ``profile_updated``
observation (the job runner normalizes it to a ``progress`` event whose
metadata keeps the marker — the frontend refreshes the profile panel on it)
and schedule ``path_rebuild`` for the new profile version.

Never raises: every failure degrades to a WARNING log line so the answering
pipeline is never disturbed. See
docs/superpowers/specs/2026-07-19-conversational-profile-building-design.md
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from tutor.core.capability_result import FollowUpTaskSpec
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile.signal_detector import detect_profile_signal
from tutor.services.learner_profile.store import get_profile_store

INGEST_TIMEOUT_SECONDS = 20.0


async def ingest_dialogue_signal(
    context: UnifiedContext,
    stream: StreamBus,
    *,
    builder: Any = None,
    extractor: Any = None,
) -> tuple[bool, tuple[FollowUpTaskSpec, ...]]:
    """Best-effort wrapper: swallow every failure, bound total latency."""
    try:
        return await asyncio.wait_for(
            _ingest(context, stream, builder=builder, extractor=extractor),
            timeout=INGEST_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 - best effort by design
        logger.warning(
            "dialogue profile ingest failed; skipped user={user}",
            user=context.user_id,
        )
        return False, ()


async def _ingest(
    context: UnifiedContext,
    stream: StreamBus,
    *,
    builder: Any,
    extractor: Any,
) -> tuple[bool, tuple[FollowUpTaskSpec, ...]]:
    from tutor.agents.profile.feature_extractor import FeatureExtractorAgent
    from tutor.services.learner_profile.builder import ProfileBuilder

    store = get_profile_store()
    builder = builder or ProfileBuilder(store=store)
    existing = await store.get(context.user_id)
    # The answering capabilities pre-create a blank profile before ingest
    # runs; a just-auto-created blank profile counts as no profile so the
    # cold-start trigger (goal-only / history-only message) still fires.
    blank = (
        existing is not None
        and existing.version <= 1
        and not existing.metadata
        and not existing.knowledge_map.scores
    )
    has_profile = existing is not None and not blank
    if not detect_profile_signal(context.user_message, has_profile=has_profile):
        return False, ()

    extractor = extractor or FeatureExtractorAgent()
    signal = await extractor.process(context, stream=stream)
    before_version = existing.version if existing is not None else 0
    updated, diff = await builder.ingest_signal(context.user_id, signal)
    if diff.is_empty() or updated.version <= before_version:
        return False, ()

    await stream.observation(
        "已从对话更新学习画像",
        source="profile_dialogue_ingest",
        stage="profile_dialogue_ingest",
        metadata={
            "profile_updated": True,
            "version": updated.version,
            "major": str(updated.metadata.get("major") or ""),
            "goal_type": updated.motivation.goal_type.value,
        },
    )

    follow_ups: list[FollowUpTaskSpec] = []
    if await store.get_path(context.user_id, updated.version) is None:
        follow_ups.append(
            FollowUpTaskSpec(
                kind="path_rebuild",
                dedupe_key=f"path_rebuild:{updated.version}",
                payload={
                    "user_id": context.user_id,
                    "profile_version": updated.version,
                    "profile": updated.model_dump(mode="json"),
                },
            )
        )
    return True, tuple(follow_ups)


__all__ = ["INGEST_TIMEOUT_SECONDS", "ingest_dialogue_signal"]
