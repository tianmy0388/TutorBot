"""Tutoring capability: dialogue ingest wiring (post-answer, best-effort)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tutor.capabilities.tutoring import TutoringCapability
from tutor.core.capability_result import FollowUpTaskSpec
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus


def _canned_agents():
    understanding = SimpleNamespace(
        to_dict=lambda: {"question_type": "concept"},
        follow_up_questions=[],
        concepts=[],
    )
    answer = SimpleNamespace(
        to_dict=lambda: {"tldr": "答"},
        tldr="答",
    )
    question_agent = SimpleNamespace(
        process=AsyncMock(return_value=understanding)
    )
    tutoring_agent = SimpleNamespace(process=AsyncMock(return_value=answer))
    enrichment_agent = SimpleNamespace(process=AsyncMock(return_value=[]))
    tutor_service = SimpleNamespace(
        record_interaction=lambda **kw: None,
        get_history=lambda user_id: [],
    )
    return question_agent, tutoring_agent, enrichment_agent, tutor_service


def _canned_collaborators():
    """Fakes for the remaining collaborators ``run()`` touches.

    Surfaces required by ``tutor.capabilities.tutoring.run()``:
    - retrieval: ``retrieve(query=..., scope=..., user_id=...)`` async,
      result needs ``.status`` / ``.chunks``.
    - search: ``execute(query, conversation_enabled=...)`` async, outcome
      needs ``.unavailable`` / ``.sources`` / ``.search_used``.
    - builder: ``get(user_id)`` async, may return ``None``.
    - event_store: ``recent_exercise_evidence(user_id, limit=...)`` async.
    """
    retrieval_service = SimpleNamespace(
        retrieve=AsyncMock(
            return_value=SimpleNamespace(status="no_evidence", chunks=[])
        )
    )
    search_executor = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(
                unavailable=False, sources=[], search_used=False
            )
        )
    )
    builder = SimpleNamespace(get=AsyncMock(return_value=None))
    event_store = SimpleNamespace(
        recent_exercise_evidence=AsyncMock(return_value=[])
    )
    return retrieval_service, search_executor, builder, event_store


def _make_capability() -> TutoringCapability:
    qa, ta, ea, ts = _canned_agents()
    rs, se, b, es = _canned_collaborators()
    return TutoringCapability(
        question_agent=qa,
        tutoring_agent=ta,
        enrichment_agent=ea,
        tutor_service=ts,
        retrieval_service=rs,
        search_executor=se,
        builder=b,
        event_store=es,
    )


def _make_context() -> UnifiedContext:
    return UnifiedContext(
        session_id="sess-1",
        user_id="user-1",
        user_message="什么是反向传播？",
        language="zh",
        capability="tutoring",
    )


@pytest.mark.asyncio
async def test_follow_up_specs_from_ingest_reach_result(monkeypatch):
    spec = FollowUpTaskSpec(
        kind="path_rebuild",
        dedupe_key="path_rebuild:2",
        payload={"user_id": "user-1", "profile_version": 2, "profile": {}},
    )
    calls = []

    async def fake_ingest(context, stream):
        calls.append(context.user_message)
        return True, (spec,)

    monkeypatch.setattr(
        "tutor.capabilities.tutoring.ingest_dialogue_signal", fake_ingest
    )
    capability = _make_capability()
    result = await capability.run(_make_context(), StreamBus())
    assert calls == ["什么是反向传播？"]
    assert spec in result.follow_up_tasks


@pytest.mark.asyncio
async def test_no_signal_leaves_follow_ups_empty(monkeypatch):
    async def fake_ingest(context, stream):
        return False, ()

    monkeypatch.setattr(
        "tutor.capabilities.tutoring.ingest_dialogue_signal", fake_ingest
    )
    capability = _make_capability()
    result = await capability.run(_make_context(), StreamBus())
    assert result.follow_up_tasks == ()
