"""Tests for :class:`ResourcePackageStore.list` summary shape.

Regression: the frontend ``/resources`` page crashed with
``Cannot read properties of undefined (reading 'map')`` because
``store.list()`` omitted the ``types`` field. The frontend renders a
per-package type chip strip, so the wire shape must include ``types``
even for header-only listings.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from tutor.services.resource_package.schema import (
    Resource,
    ResourcePackage,
    ResourceType,
)
from tutor.services.resource_package.store import (
    ResourcePackageStore,
    reset_resource_package_store,
)


@pytest.fixture
def fresh_store(tmp_path, monkeypatch):
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    reset_resource_package_store()
    yield ResourcePackageStore()
    reset_resource_package_store()


def _build_pkg(
    user_id: str,
    *,
    topic: str,
    types: list[ResourceType],
    confidences: list[float] | None = None,
) -> ResourcePackage:
    confidences = confidences or [0.7] * len(types)
    resources = [
        Resource(
            type=t,
            title=f"{t.value}-{i}",
            confidence_score=c,
        )
        for i, (t, c) in enumerate(zip(types, confidences))
    ]
    pkg = ResourcePackage(
        package_id=f"pkg_{uuid.uuid4().hex[:12]}",
        topic=topic,
        resources=resources,
    )
    pkg.metadata["user_id"] = user_id
    return pkg


@pytest.mark.asyncio
async def test_list_includes_types_per_package(fresh_store) -> None:
    pkg_a = _build_pkg(
        "u1",
        topic="Transformer",
        types=[ResourceType.DOCUMENT, ResourceType.MINDMAP, ResourceType.EXERCISE],
    )
    pkg_b = _build_pkg(
        "u1",
        topic="CPU 调度",
        types=[ResourceType.VIDEO],
    )
    await fresh_store.save(pkg_a, user_id="u1")
    await fresh_store.save(pkg_b, user_id="u1")

    items = await fresh_store.list("u1")
    assert len(items) == 2
    by_topic = {p["topic"]: p for p in items}
    # The /resources page reads p.types.map(...) — types must be a list
    # of strings on every entry, never undefined.
    for entry in items:
        assert isinstance(entry.get("types"), list), (
            f"summary missing types: keys={sorted(entry)}"
        )
        for t in entry["types"]:
            assert isinstance(t, str)
    assert sorted(by_topic["Transformer"]["types"]) == [
        "document",
        "exercise",
        "mindmap",
    ]
    assert by_topic["CPU 调度"]["types"] == ["video"]


@pytest.mark.asyncio
async def test_list_empty_returns_empty_list(fresh_store) -> None:
    items = await fresh_store.list("u-nobody")
    assert items == []


@pytest.mark.asyncio
async def test_list_filters_by_user(fresh_store) -> None:
    pkg_a = _build_pkg("alice", topic="T1", types=[ResourceType.DOCUMENT])
    pkg_b = _build_pkg("bob", topic="T2", types=[ResourceType.EXERCISE])
    await fresh_store.save(pkg_a, user_id="alice")
    await fresh_store.save(pkg_b, user_id="bob")

    alice_items = await fresh_store.list("alice")
    bob_items = await fresh_store.list("bob")
    assert {p["topic"] for p in alice_items} == {"T1"}
    assert {p["topic"] for p in bob_items} == {"T2"}
    assert alice_items[0]["types"] == ["document"]
    assert bob_items[0]["types"] == ["exercise"]


@pytest.mark.asyncio
async def test_list_respects_limit_and_topic_filter(fresh_store) -> None:
    for i in range(3):
        await fresh_store.save(
            _build_pkg(
                "u1",
                topic=f"Transformer 主题 {i}",
                types=[ResourceType.DOCUMENT],
            ),
            user_id="u1",
        )
    await fresh_store.save(
        _build_pkg("u1", topic="其他", types=[ResourceType.EXERCISE]),
        user_id="u1",
    )

    # Limit
    items = await fresh_store.list("u1", limit=2)
    assert len(items) == 2

    # Topic filter
    items = await fresh_store.list("u1", topic="Transformer")
    assert len(items) == 3
    for p in items:
        assert "Transformer" in p["topic"]
        assert p["types"] == ["document"]
