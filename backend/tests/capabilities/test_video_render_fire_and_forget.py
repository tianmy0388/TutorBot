"""Regression test: video rendering must NOT block ``cap.run()``.

fdb26152 trace analysis (Phase 5): the previous ``_render_pending_videos``
was awaited inline, so a slow Manim encode pushed the entire job past
the 600s timeout — even though every resource was already streamable.
We split it into ``_start_pending_video_renders`` (returns immediately)
+ ``_render_one_video`` (background task that emits a fresh ``RESOURCE``
event when done).

These tests pin:
  * ``_start_pending_video_renders`` returns synchronously
    (cap.run() can emit ``done`` without waiting for manim).
  * Each pending video gets exactly one background task.
  * A successful render emits a ``RESOURCE`` event with the updated
    ``render_status == "ready"`` + ``video_url`` so the right-pane
    card swaps the placeholder for a real video player.
  * A render exception emits ``RESOURCE`` with ``render_status ==
    "failed"`` (so the UI never shows a forever-pending card).
  * The reference list ``self._bg_render_tasks`` retains the tasks
    so asyncio doesn't GC them mid-render.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.resource_package.schema import (
    Resource,
    ResourcePackage,
    ResourceType,
    VideoResource,
)


class _FakeRenderService:
    """Stand-in for ``ManimRenderService`` — controllable success/failure."""

    def __init__(self, *, success: bool = True, delay: float = 0.0) -> None:
        self.success = success
        self.delay = delay
        self.calls: list[tuple[str, str]] = []

    async def render(self, *, code: str, scene_class: str):  # type: ignore[no-untyped-def]
        self.calls.append((code, scene_class))
        if self.delay > 0:
            await asyncio.sleep(self.delay)

        class _R:
            pass

        r = _R()
        r.success = self.success
        r.public_url = "https://cdn.example.com/v.mp4" if self.success else None
        r.video_path = "/tmp/v.mp4" if self.success else None
        r.duration_seconds = 30.0 if self.success else None
        r.error = None if self.success else "manim exit 1"
        r.attempts = 1
        return r


def _video_resource(render_status: str = "pending", *, code: str = "class M(Scene): pass") -> Resource:
    return Resource(
        type=ResourceType.VIDEO,
        title="反向传播",
        content="video body",
        topic="反向传播",
        format_specific=VideoResource(
            manim_code=code,
            scene_class="M",
            render_status=render_status,  # type: ignore[arg-type]
        ).model_dump(),
    )


def _cap() -> Any:
    """Build a real ResourceGenerationCapability. We pass mocks for
    every Agent so no LLM / DB is touched. Pydantic v2 BaseCapability
    is frozen-ish, so we have to go through ``__init__``.
    """
    from tutor.capabilities.resource_generation import ResourceGenerationCapability

    # Cheap stubs: never invoked by the render path.
    class _Stub:
        async def process(self, *a, **kw):
            raise NotImplementedError

    cap = ResourceGenerationCapability(
        intent_agent=_Stub(),  # type: ignore[arg-type]
        content_expert=_Stub(),  # type: ignore[arg-type]
        pedagogy=_Stub(),  # type: ignore[arg-type]
        multimedia=_Stub(),  # type: ignore[arg-type]
        exercise_generator=_Stub(),  # type: ignore[arg-type]
        manim_video=_Stub(),  # type: ignore[arg-type]
        code_sandbox=_Stub(),  # type: ignore[arg-type]
        quality_reviewer=_Stub(),  # type: ignore[arg-type]
        anti_hallucination=_Stub(),  # type: ignore[arg-type]
        ppt_generator=_Stub(),  # type: ignore[arg-type]
    )
    return cap


@pytest.mark.asyncio
async def test_start_returns_immediately_without_awaiting_render() -> None:
    """Even with a slow render, ``_start_pending_video_renders``
    returns within a few ms — not after the render completes."""
    cap = _cap()
    fake = _FakeRenderService(success=True, delay=2.0)
    # Monkey-patch the service lookup the inner method uses.
    from tutor.services import manim_render as mr_module
    monkey = monkeypatch_for_module(mr_module, fake)

    bus = StreamBus()
    pkg = ResourcePackage(topic="t", resources=[_video_resource()])
    ctx = UnifiedContext(language="zh")

    t0 = asyncio.get_event_loop().time()
    tasks = await cap._start_pending_video_renders(pkg, ctx, bus)
    elapsed = asyncio.get_event_loop().time() - t0

    assert len(tasks) == 1
    assert elapsed < 0.2, (
        f"_start_pending_video_renders blocked {elapsed:.2f}s "
        f"(should return in <0.2s, render runs in background)"
    )
    # The task is still pending — render hasn't finished yet.
    assert not tasks[0].done()

    # Now await it; render completes (must do this BEFORE undo so
    # the background task still sees the monkeypatched service).
    await asyncio.gather(*tasks, return_exceptions=True)
    monkey.undo()
    assert fake.calls == [("class M(Scene): pass", "M")]


@pytest.mark.asyncio
async def test_render_success_emits_resource_event_with_ready_status() -> None:
    """When the render succeeds, ``_render_one_video`` must emit a
    fresh ``RESOURCE`` event with ``render_status="ready"`` and the
    new ``video_url``, so the frontend card swaps the placeholder.
    """
    cap = _cap()
    fake = _FakeRenderService(success=True)
    from tutor.services import manim_render as mr_module
    monkey = monkeypatch_for_module(mr_module, fake)

    bus = StreamBus()
    q = bus.subscribe()
    res = _video_resource()
    pkg = ResourcePackage(topic="t", resources=[res])
    ctx = UnifiedContext(language="zh")

    await cap._render_one_video(res, pkg, ctx, bus)
    monkey.undo()

    # Drain events until we see the RESOURCE one — the helper emits
    # an observation first, then RESOURCE.
    async def _wait_resource() -> Any:
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            evt = await asyncio.wait_for(q.get(), timeout=2.0)
            if evt.type.value == "resource":
                return evt
        raise AssertionError("no RESOURCE event received within 2s")

    evt = await _wait_resource()
    md = evt.metadata
    assert md["resource_id"] == res.resource_id
    assert md["resource_type"] == "video"
    # The full resource dict is now updated to "ready" + has video_url
    payload = md["resource"]
    assert isinstance(payload, dict), (
        f"resource payload must be a dict, got {type(payload).__name__}: "
        f"{payload!r:.200}"
    )
    assert payload["format_specific"]["render_status"] == "ready"
    assert payload["format_specific"]["video_url"] == "https://cdn.example.com/v.mp4"


@pytest.mark.asyncio
async def test_render_failure_emits_resource_event_with_failed_status() -> None:
    """A render exception must still emit ``RESOURCE`` so the right
    pane shows '渲染失败' instead of a forever-pending placeholder."""
    cap = _cap()
    fake = _FakeRenderService(success=False)
    from tutor.services import manim_render as mr_module
    monkey = monkeypatch_for_module(mr_module, fake)

    bus = StreamBus()
    q = bus.subscribe()
    res = _video_resource()
    pkg = ResourcePackage(topic="t", resources=[res])
    ctx = UnifiedContext(language="zh")

    await cap._render_one_video(res, pkg, ctx, bus)
    monkey.undo()

    async def _wait_resource() -> Any:
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            evt = await asyncio.wait_for(q.get(), timeout=2.0)
            if evt.type.value == "resource":
                return evt
        raise AssertionError("no RESOURCE event received within 2s")

    evt = await _wait_resource()
    md = evt.metadata
    payload = md["resource"]
    assert payload["format_specific"]["render_status"] == "failed"
    assert "manim exit 1" in payload["format_specific"].get("render_error", "")


@pytest.mark.asyncio
async def test_no_pending_videos_returns_empty_task_list() -> None:
    """Edge case: no VIDEO resources at all → no background tasks.
    Must not block, must not error."""
    cap = _cap()
    bus = StreamBus()
    pkg = ResourcePackage(topic="t", resources=[])  # no videos
    ctx = UnifiedContext(language="zh")

    tasks = await cap._start_pending_video_renders(pkg, ctx, bus)
    assert tasks == []


@pytest.mark.asyncio
async def test_already_ready_videos_are_skipped() -> None:
    """Only ``render_status == "pending"`` videos are rendered;
    already-``ready`` ones (e.g. retry) are not re-rendered."""
    cap = _cap()
    fake = _FakeRenderService(success=True)
    from tutor.services import manim_render as mr_module
    monkey = monkeypatch_for_module(mr_module, fake)

    bus = StreamBus()
    pkg = ResourcePackage(
        topic="t",
        resources=[_video_resource(render_status="ready")],
    )
    ctx = UnifiedContext(language="zh")

    tasks = await cap._start_pending_video_renders(pkg, ctx, bus)
    monkey.undo()
    assert tasks == []
    assert fake.calls == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MonkeyPatch:
    """Minimal ``monkeypatch.setattr`` replacement so the tests don't
    need to take ``monkeypatch`` as a fixture (avoids pytest fixture
    ordering quirks)."""

    _MISSING = object()

    def __init__(self) -> None:
        self._undo_stack: list[Any] = []

    def setattr(self, target: Any, name: str, value: Any) -> None:
        old = getattr(target, name, self._MISSING)
        setattr(target, name, value)
        self._undo_stack.append((target, name, old))

    def undo(self) -> None:
        while self._undo_stack:
            target, name, old = self._undo_stack.pop()
            if old is self._MISSING:
                try:
                    delattr(target, name)
                except AttributeError:
                    pass
            else:
                setattr(target, name, old)


def monkeypatch_for_module(mod: Any, fake: Any) -> _MonkeyPatch:
    """Replace ``get_manim_render_service`` in the manim_render
    sub-module with a callable that returns ``fake``.

    The capability lazy-imports from
    ``tutor.services.manim_render.service``, so we must monkeypatch
    THAT submodule, not the package ``__init__``.
    """
    m = _MonkeyPatch()
    # Patch both the submodule the capability imports from and the
    # package-level alias, just in case.
    from tutor.services.manim_render import service as service_mod
    m.setattr(service_mod, "get_manim_render_service", lambda: fake)
    m.setattr(mod, "get_manim_render_service", lambda: fake)
    return m


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))