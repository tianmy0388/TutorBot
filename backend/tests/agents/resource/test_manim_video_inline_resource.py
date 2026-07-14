"""Regression test: manim_video agent must emit a RESOURCE event
from INSIDE the agent.

**2026-07-08 fix (585f367d trace):** the user's session ran out at
600s with ``video_code_generation stage_end`` already on the wire but
NO ``RESOURCE`` event for the video. The capability's
``_generate_parallel`` only emits RESOURCE after ``as_completed``
yields the video task — and if the timeout fires while as_completed
is blocked on a slower sibling (e.g. mindmap), the video emit never
runs and the card disappears from the right pane.

After this fix, ``ManimVideoAgent.process`` emits its own
``RESOURCE`` event right before returning, so the event lands in
the bus as soon as the agent finishes — independent of caller
cancellation.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

import pytest

from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.resource_package.schema import ResourceType


class _FakeLLM:
    """Returns canned responses for the two LLM stages."""

    model = "fake"

    def __init__(self, concept_resp: str, code_resp: str) -> None:
        self._script = [concept_resp, code_resp]
        self.calls = 0

    async def call(self, req: Any):  # type: ignore[no-untyped-def]
        idx = self.calls
        self.calls += 1
        content = self._script[idx] if idx < len(self._script) else "{}"
        from tutor.services.llm.base import LLMResponse
        return LLMResponse(content=content, model=self.model, finish_reason="stop")


@pytest.mark.asyncio
async def test_manim_video_emits_resource_event_inline() -> None:
    """Successful path: the agent must emit a RESOURCE event whose
    payload is the just-built :class:`Resource`. The event must
    carry ``format_specific.render_status == "pending"`` so the
    right-pane card renders the "渲染中" placeholder."""
    from tutor.agents.resource.manim_video import ManimVideoAgent

    concept = (
        '{"concept": "反向传播示意", "duration_seconds": 30,'
        ' "scenes": [{"name": "intro", "duration_seconds": 30}]}'
    )
    code = (
        "from manim import *\n"
        "class BP(Scene):\n"
        "    def construct(self):\n"
        "        self.play(Write(Text('BP')))\n"
    )
    llm = _FakeLLM(concept, code)

    agent = ManimVideoAgent(llm=llm)  # type: ignore[arg-type]
    bus = StreamBus()
    q = bus.subscribe()
    ctx = UnifiedContext(language="zh", user_message="什么是反向传播？")

    resource = await agent.process(
        context=ctx,
        stream=bus,
        topic="反向传播",
    )

    assert resource is not None
    assert resource.type == ResourceType.VIDEO

    # Drain bus events until we see the RESOURCE one.
    async def _wait_resource() -> Any:
        deadline = asyncio.get_event_loop().time() + 2.0
        while asyncio.get_event_loop().time() < deadline:
            evt = await asyncio.wait_for(q.get(), timeout=2.0)
            if evt.type.value == "resource":
                return evt
        raise AssertionError("no RESOURCE event received within 2s")

    evt = await _wait_resource()
    md = evt.metadata
    assert md["resource_id"] == resource.resource_id
    assert md["resource_type"] == "video"
    payload = md["resource"]
    assert isinstance(payload, dict)
    assert payload["format_specific"]["render_status"] == "pending"
    assert payload["format_specific"]["scene_class"] == "BP"


@pytest.mark.asyncio
async def test_manim_video_emits_resource_even_when_caller_cancels() -> None:
    """The whole point of the inline emit: the event lands in the
    bus BEFORE the agent returns, so a caller-side cancellation
    (timeout, asyncio.CancelledError, etc.) does NOT swallow the
    video card. We simulate that by reading events immediately
    after ``agent.process`` finishes — the emit must already be
    in the subscriber queue."""
    from tutor.agents.resource.manim_video import ManimVideoAgent

    concept = (
        '{"concept": "反向传播", "duration_seconds": 30,'
        ' "scenes": [{"name": "intro", "duration_seconds": 30}]}'
    )
    code = (
        "from manim import *\n"
        "class BP(Scene):\n"
        "    def construct(self):\n"
        "        self.play(Write(Text('BP')))\n"
    )
    llm = _FakeLLM(concept, code)
    agent = ManimVideoAgent(llm=llm)  # type: ignore[arg-type]
    bus = StreamBus()
    q = bus.subscribe()
    ctx = UnifiedContext(language="zh", user_message="什么是反向传播？")

    await agent.process(context=ctx, stream=bus, topic="反向传播")

    # The RESOURCE event must already be in the queue (or have
    # been emitted and consumed) — NOT waiting on any caller-side
    # post-processing. We inspect the queue non-blockingly.
    drained: list[str] = []
    while True:
        try:
            evt = q.get_nowait()
        except asyncio.QueueEmpty:
            break
        drained.append(evt.type.value)
    assert "resource" in drained, (
        f"expected RESOURCE event in queue before caller post-processing; "
        f"saw only {drained!r}"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))