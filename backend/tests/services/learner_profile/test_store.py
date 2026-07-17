"""Tests for :mod:`tutor.services.learner_profile.store`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from tutor.services.learner_profile.schema import (
    CognitiveStyle,
    LearnerProfile,
    ProfileDiff,
    apply_diff,
    empty_profile,
)
from tutor.services.learner_profile.store import (
    ProfileEventType,
    ProfileStore,
)


@pytest.fixture
async def store(tmp_path: Path) -> ProfileStore:
    s = ProfileStore(tmp_path / "test_profiles.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_get_or_create_blank(store: ProfileStore):
    p = await store.get_or_create("alice")
    assert isinstance(p, LearnerProfile)
    assert p.user_id == "alice"
    assert p.version == 1
    assert len(p.knowledge_map.scores) == 0


@pytest.mark.asyncio
async def test_save_and_reload(store: ProfileStore):
    p = await store.get_or_create("bob")
    apply_diff(p, ProfileDiff(knowledge_delta={"LSTM": 0.3}))
    await store.save(p, source="test")

    p2 = await store.get_or_create("bob")
    assert p2.version == p.version
    assert p2.knowledge_map.get("LSTM") == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_apply_diff_through_store(store: ProfileStore):
    p = await store.apply_diff(
        "carol",
        ProfileDiff(
            knowledge_delta={"X": 0.5},
            cognitive_style=CognitiveStyle.VISUAL,
        ),
        source="unit_test",
    )
    assert p.knowledge_map.get("X") == pytest.approx(0.5)
    assert p.cognitive_style == CognitiveStyle.VISUAL
    assert p.version >= 2


@pytest.mark.asyncio
async def test_apply_empty_diff_returns_current(store: ProfileStore):
    p1 = await store.apply_diff("dave", ProfileDiff())
    p2 = await store.apply_diff("dave", ProfileDiff())
    assert p1.version == p2.version


@pytest.mark.asyncio
async def test_history_records_all_writes(store: ProfileStore):
    await store.get_or_create("eve")  # CREATED event
    await store.apply_diff("eve", ProfileDiff(knowledge_delta={"X": 0.1}))  # DIFF_APPLIED
    await store.apply_diff("eve", ProfileDiff(knowledge_delta={"Y": 0.2}))  # DIFF_APPLIED
    history = await store.history("eve", limit=10)
    assert len(history) >= 3
    # Most recent first
    assert history[0].event_type in (ProfileEventType.DIFF_APPLIED,)
    sources = [e.source for e in history]
    assert "ProfileStore" in sources  # initial creation
    assert any(s.startswith("agent") for s in sources)


@pytest.mark.asyncio
async def test_replace_overwrites(store: ProfileStore):
    await store.apply_diff("frank", ProfileDiff(knowledge_delta={"A": 0.5}))
    fresh = empty_profile(user_id="frank")
    fresh.knowledge_map.set("B", 0.9)
    await store.replace(fresh, source="reset")
    p = await store.get_or_create("frank")
    assert p.knowledge_map.get("A") == 0.0  # wiped
    assert p.knowledge_map.get("B") == pytest.approx(0.9)


@pytest.mark.asyncio
async def test_delete(store: ProfileStore):
    await store.apply_diff("gina", ProfileDiff(knowledge_delta={"X": 0.1}))
    deleted = await store.delete("gina")
    assert deleted is True
    p = await store.get_or_create("gina")
    # After delete + get_or_create, fresh blank
    assert p.version == 1
    assert len(p.knowledge_map.scores) == 0


@pytest.mark.asyncio
async def test_list_users(store: ProfileStore):
    await store.get_or_create("u1")
    await store.get_or_create("u2")
    users = await store.list_users()
    assert "u1" in users
    assert "u2" in users


@pytest.mark.asyncio
async def test_stats(store: ProfileStore):
    await store.apply_diff("hugo", ProfileDiff(knowledge_delta={"X": 0.8}))
    stats = await store.stats("hugo")
    assert stats["summary"]["knowledge_count"] == 1
    assert stats["summary"]["user_id"] == "hugo"
    assert stats["last_event"] is not None


@pytest.mark.asyncio
async def test_concurrent_apply_diff_safe(store: ProfileStore):
    """Concurrent applies should produce a monotonically advancing version."""
    await store.get_or_create("iris")

    async def bump(i: int):
        return await store.apply_diff(
            "iris", ProfileDiff(knowledge_delta={f"k{i}": 0.1}), source=f"task-{i}"
        )

    results = await asyncio.gather(*[bump(i) for i in range(10)])
    versions = [r.version for r in results]
    assert versions == sorted(versions), f"versions not monotonic: {versions}"
    assert versions[-1] >= 11  # 1 initial + 10 diffs

    final = await store.get_or_create("iris")
    assert final.version == versions[-1]
    for i in range(10):
        assert final.knowledge_map.get(f"k{i}") == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_get_does_not_fabricate_an_empty_profile(store: ProfileStore):
    assert await store.get("missing") is None
    assert await store.list_users() == []


@pytest.mark.asyncio
async def test_event_profile_cas_advances_watermark_once(store: ProfileStore):
    candidate = LearnerProfile(user_id="u", event_watermark=5)
    candidate.knowledge_map.set("attention", 0.7)

    saved = await store.save_event_profile(candidate, expected_watermark=0)
    stale = await store.save_event_profile(candidate, expected_watermark=0)

    assert saved.applied is True
    assert saved.profile.version == 2
    assert saved.profile.event_watermark == 5
    assert stale.applied is False
    assert stale.profile.version == 2


@pytest.mark.asyncio
async def test_event_profile_cas_rejects_same_watermark_newer_profile_version(
    store: ProfileStore,
):
    base = await store.get_or_create("u-version-fence")
    candidate = base.model_copy(deep=True)
    candidate.event_watermark = 5
    candidate.knowledge_map.set("attention", 0.7)
    concurrent = await store.apply_diff(
        "u-version-fence",
        ProfileDiff(metadata_merge={"concurrent_marker": "preserve-me"}),
        source="concurrent-profile-writer",
    )
    assert concurrent.event_watermark == base.event_watermark
    assert concurrent.version > base.version

    stale = await store.save_event_profile(candidate, expected_watermark=0)

    assert stale.applied is False
    assert stale.profile.version == concurrent.version
    assert stale.profile.metadata["concurrent_marker"] == "preserve-me"


@pytest.mark.asyncio
async def test_path_is_unique_per_profile_version_and_latest_is_durable(
    store: ProfileStore,
):
    from tutor.services.learner_profile.schema import PersistedLearningPath

    first = PersistedLearningPath(
        user_id="u",
        profile_version=2,
        nodes=[{"id": "attention", "status": "available"}],
        edges=[],
        rationale="mastery-aware topological order",
    )
    duplicate = first.model_copy(update={"rationale": "retry must not overwrite"})

    assert (await store.save_path(first)).rationale == first.rationale
    assert (await store.save_path(duplicate)).rationale == first.rationale
    assert (await store.get_path("u", 2)).profile_version == 2
    assert (await store.get_latest_path("u")).nodes[0]["id"] == "attention"
