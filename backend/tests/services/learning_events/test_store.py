"""Tests for :mod:`tutor.services.learning_events.store`."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tutor.services.learning_events.schema import (
    EventType,
    LearningEvent,
)
from tutor.services.learning_events.store import (
    LearningEventStore,
)


@pytest.fixture
async def store(tmp_path):
    s = LearningEventStore(tmp_path / "test_events.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_record_and_query(store):
    e = LearningEvent(
        user_id="alice",
        event_type=EventType.EXERCISE_COMPLETED,
        target_id="ex-001",
        concept_id="LSTM",
        score=0.85,
    )
    await store.record(e)
    events = await store.query("alice")
    assert len(events) == 1
    assert events[0].user_id == "alice"
    assert events[0].score == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_record_many(store):
    events = [
        LearningEvent(
            user_id="alice",
            event_type=EventType.RESOURCE_VIEWED,
            target_id=f"r-{i}",
        )
        for i in range(5)
    ]
    count = await store.record_many(events)
    assert count == 5
    got = await store.query("alice")
    assert len(got) == 5


@pytest.mark.asyncio
async def test_query_filters_by_event_type(store):
    await store.record_many([
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_VIEWED, target_id="v"),
        LearningEvent(user_id="u", event_type=EventType.EXERCISE_COMPLETED, target_id="e"),
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_VIEWED, target_id="v2"),
    ])
    only_views = await store.query("u", event_types=[EventType.RESOURCE_VIEWED])
    assert len(only_views) == 2
    assert all(e.event_type == EventType.RESOURCE_VIEWED for e in only_views)


@pytest.mark.asyncio
async def test_query_filters_by_concept(store):
    await store.record_many([
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_VIEWED, concept_id="LSTM"),
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_VIEWED, concept_id="RNN"),
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_VIEWED, concept_id="LSTM"),
    ])
    lstm = await store.query("u", concept_id="LSTM")
    assert len(lstm) == 2


@pytest.mark.asyncio
async def test_query_filters_by_time(store):
    now = datetime.now(timezone.utc)
    await store.record_many([
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_VIEWED, target_id="old", created_at=now - timedelta(days=10)),
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_VIEWED, target_id="new"),
    ])
    recent = await store.query("u", since=now - timedelta(hours=1))
    assert len(recent) == 1
    assert recent[0].target_id == "new"


@pytest.mark.asyncio
async def test_stats_empty(store):
    stats = await store.stats("nobody")
    assert stats["event_count"] == 0
    assert stats["exercise_score_avg"] is None
    assert stats["completion_rate"] == 0.0


@pytest.mark.asyncio
async def test_stats_with_events(store):
    await store.record_many([
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_VIEWED, target_id="r1"),
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_VIEWED, target_id="r2"),
        LearningEvent(user_id="u", event_type=EventType.RESOURCE_COMPLETED, target_id="r1"),
        LearningEvent(user_id="u", event_type=EventType.EXERCISE_COMPLETED, target_id="e1", score=0.8, concept_id="LSTM"),
        LearningEvent(user_id="u", event_type=EventType.EXERCISE_COMPLETED, target_id="e2", score=0.6, concept_id="LSTM"),
        LearningEvent(user_id="u", event_type=EventType.EXERCISE_COMPLETED, target_id="e3", score=0.9, concept_id="RNN"),
    ])
    stats = await store.stats("u")
    assert stats["event_count"] == 6
    assert stats["by_type"]["resource_viewed"] == 2
    assert stats["by_type"]["resource_completed"] == 1
    assert stats["exercise_score_avg"] == pytest.approx((0.8 + 0.6 + 0.9) / 3)
    assert stats["completion_rate"] == pytest.approx(0.5)  # 1 completed / 2 viewed
    assert set(stats["concepts_touched"]) == {"LSTM", "RNN"}


@pytest.mark.asyncio
async def test_list_users(store):
    await store.record_many([
        LearningEvent(user_id="alice", event_type=EventType.RESOURCE_VIEWED, target_id="x"),
        LearningEvent(user_id="bob", event_type=EventType.RESOURCE_VIEWED, target_id="x"),
        LearningEvent(user_id="alice", event_type=EventType.RESOURCE_VIEWED, target_id="y"),
    ])
    users = await store.list_users()
    assert "alice" in users
    assert "bob" in users


@pytest.mark.asyncio
async def test_correct_field_stored(store):
    """Test the 0/1/null mapping of the `correct` column."""
    await store.record_many([
        LearningEvent(user_id="u", event_type=EventType.EXERCISE_COMPLETED, target_id="a", correct=True),
        LearningEvent(user_id="u", event_type=EventType.EXERCISE_COMPLETED, target_id="b", correct=False),
        LearningEvent(user_id="u", event_type=EventType.EXERCISE_COMPLETED, target_id="c"),
    ])
    events = await store.query("u")
    by_id = {e.target_id: e.correct for e in events}
    assert by_id["a"] is True
    assert by_id["b"] is False
    assert by_id["c"] is None


@pytest.mark.asyncio
async def test_score_field_roundtrip(store):
    """Test the int*1000 mapping of the score column."""
    await store.record(
        LearningEvent(
            user_id="u",
            event_type=EventType.EXERCISE_COMPLETED,
            target_id="x",
            score=0.8765,
        )
    )
    events = await store.query("u")
    assert events[0].score == pytest.approx(0.877, abs=1e-3)  # roundtrip with precision loss


@pytest.mark.asyncio
async def test_concurrent_record_safety(store):
    """Multiple concurrent records should all succeed."""
    tasks = [
        store.record(
            LearningEvent(
                user_id="u", event_type=EventType.RESOURCE_VIEWED, target_id=f"t-{i}"
            )
        )
        for i in range(20)
    ]
    await asyncio.gather(*tasks)
    events = await store.query("u")
    assert len(events) == 20
