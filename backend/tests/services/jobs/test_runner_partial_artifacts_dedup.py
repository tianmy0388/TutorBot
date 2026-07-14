"""Regression test: ``_materialize_partial_artifacts`` must dedup by
``resource_id``.

**2026-07-08 fix (039b4a70 trace):** pre-fix, when the same
``RESOURCE`` event fired twice for one resource (e.g. once from
``manim_video``'s inline emit at agent-return time, then again from
``_generate_parallel``'s ``as_completed`` yield), both copies ended
up in ``contract.partial_artifacts``. The frontend then iterated
the duplicates and pushed the same resource into
``latestPackage.resources`` twice, triggering React's
``Encountered two children with the same key, …`` error.

The dedup MUST be at the runner level so the contract is canonical,
and we add a frontend-level dedup as a defense-in-depth check in
``buildPartialPackageFromContract``.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest


def _entry(resource_id: str | None, resource_type: str = "video") -> dict[str, Any]:
    return {
        "resource_type": resource_type,
        "status": "succeeded",
        "resource_id": resource_id,
        "title": "t",
        "metadata": {"source_event_seq": 1},
    }


def test_dedup_keeps_first_occurrence() -> None:
    from tutor.services.jobs.runner import _materialize_partial_artifacts

    raw = [
        _entry("vid-1", "video"),
        _entry("doc-1", "document"),
        _entry("vid-1", "video"),  # duplicate
    ]
    out = _materialize_partial_artifacts(raw)
    ids = [a.resource_id for a in out]
    assert ids.count("vid-1") == 1, (
        f"duplicate resource_id was not deduped: {ids!r}"
    )
    assert ids == ["vid-1", "doc-1"]


def test_dedup_preserves_order() -> None:
    from tutor.services.jobs.runner import _materialize_partial_artifacts

    raw = [
        _entry("a"),
        _entry("b"),
        _entry("a"),  # dup of first
        _entry("c"),
        _entry("b"),  # dup of second
    ]
    out = _materialize_partial_artifacts(raw)
    assert [a.resource_id for a in out] == ["a", "b", "c"]


def test_no_dedup_needed_when_unique() -> None:
    """Sanity: unique resource_ids are not deduped (would change order
    or lose data)."""
    from tutor.services.jobs.runner import _materialize_partial_artifacts

    raw = [_entry("a"), _entry("b"), _entry("c")]
    out = _materialize_partial_artifacts(raw)
    assert [a.resource_id for a in out] == ["a", "b", "c"]


def test_entries_without_resource_id_are_kept() -> None:
    """Defensive: a malformed partial_artifact with no resource_id
    (e.g. legacy event without one) still flows through. The dedup
    operates on resource_id; entries without one are kept verbatim
    because they can't conflict."""
    from tutor.services.jobs.runner import _materialize_partial_artifacts

    raw = [
        _entry("vid-1"),
        _entry(None),
        _entry("vid-1"),
    ]
    out = _materialize_partial_artifacts(raw)
    ids = [a.resource_id for a in out]
    # vid-1 deduped to 1; the None entry is kept.
    assert ids.count("vid-1") == 1
    assert None in ids


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))