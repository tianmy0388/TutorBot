from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
from tutor.agents.resource.ppt_generator import PPTGeneratorAgent
from tutor.core.stream_bus import StreamBus
from tutor.services.ppt.service import (
    PPTGenerationService,
    Slide,
    render_slides,
)


class _CooperativeBlockingRenderer:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancel_seen = threading.Event()
        self.finished = threading.Event()
        self.render_path: Path | None = None

    def __call__(
        self,
        slides: Any,
        output_path: Path,
        *,
        title: str = "",
        cancel_event: threading.Event | None = None,
    ) -> Path:
        del slides, title
        self.render_path = output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"private-partial-pptx")
        self.entered.set()
        try:
            while not self.release.wait(timeout=0.005):
                if cancel_event is not None and cancel_event.is_set():
                    self.cancel_seen.set()
                    assert self.release.wait(timeout=5)
                    raise RuntimeError("cancelled by test")
            return output_path
        finally:
            self.finished.set()


async def _wait_thread_event(event: threading.Event) -> None:
    deadline = monotonic() + 2
    while not event.is_set() and monotonic() < deadline:
        await asyncio.sleep(0.005)
    assert event.is_set()


def _artifact_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


def _render_complete(
    slides: Any,
    output_path: Path,
    *,
    title: str = "",
    cancel_event: threading.Event | None = None,
) -> Path:
    del slides, title
    assert cancel_event is not None and not cancel_event.is_set()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(b"complete-pptx")
    return output_path


@pytest.mark.asyncio
async def test_cancelled_ppt_worker_never_publishes_or_emits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await asyncio.to_thread(lambda: None)
    baseline_threads = {thread.ident for thread in threading.enumerate()}
    renderer = _CooperativeBlockingRenderer()
    monkeypatch.setattr("tutor.services.ppt.service.render_slides", renderer)
    service = PPTGenerationService(output_dir=tmp_path / "ppt")
    agent = PPTGeneratorAgent(ppt_service=service)
    stream = StreamBus()
    queue = stream.subscribe()

    task = asyncio.create_task(
        agent.process(
            topic="Cancellation",
            source_content="# Cancellation\n\n## One\ncontent",
            stream=stream,
        )
    )
    await _wait_thread_event(renderer.entered)
    task.cancel()
    try:
        await _wait_thread_event(renderer.cancel_seen)
        assert not task.done(), "cancellation returned before the worker cleaned up"
        renderer.release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        renderer.release.set()

    assert renderer.finished.is_set()
    assert {thread.ident for thread in threading.enumerate()} == baseline_threads
    assert _artifact_files(tmp_path / "ppt") == []
    assert queue.empty()


@pytest.mark.asyncio
async def test_timed_out_ppt_worker_never_publishes_or_emits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = _CooperativeBlockingRenderer()
    monkeypatch.setattr("tutor.services.ppt.service.render_slides", renderer)
    service = PPTGenerationService(output_dir=tmp_path / "ppt")
    agent = PPTGeneratorAgent(ppt_service=service)
    stream = StreamBus()
    queue = stream.subscribe()

    async def run_with_timeout() -> None:
        async with asyncio.timeout(0.05):
            await agent.process(
                topic="Timeout",
                source_content="# Timeout\n\n## One\ncontent",
                stream=stream,
            )

    task = asyncio.create_task(run_with_timeout())
    await _wait_thread_event(renderer.entered)
    try:
        await _wait_thread_event(renderer.cancel_seen)
        assert not task.done(), "timeout returned before the worker cleaned up"
        renderer.release.set()
        with pytest.raises(TimeoutError):
            await task
    finally:
        renderer.release.set()

    assert renderer.finished.is_set()
    assert _artifact_files(tmp_path / "ppt") == []
    assert queue.empty()


@pytest.mark.asyncio
async def test_cancel_during_publish_barrier_removes_canonical_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replace_entered = threading.Event()
    release_replace = threading.Event()
    original_replace = Path.replace

    def render(
        slides: Any,
        output_path: Path,
        *,
        title: str = "",
        cancel_event: threading.Event | None = None,
    ) -> Path:
        del slides, title, cancel_event
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"complete-before-publish")
        return output_path

    def blocking_replace(path: Path, target: Path) -> Path:
        replace_entered.set()
        assert release_replace.wait(timeout=5)
        return original_replace(path, target)

    monkeypatch.setattr("tutor.services.ppt.service.render_slides", render)
    monkeypatch.setattr(Path, "replace", blocking_replace)
    service = PPTGenerationService(output_dir=tmp_path / "ppt")
    agent = PPTGeneratorAgent(ppt_service=service)
    task = asyncio.create_task(
        agent.process(
            topic="Publish race",
            source_content="# Publish race\n\n## One\ncontent",
        )
    )
    await _wait_thread_event(replace_entered)
    task.cancel()
    try:
        await asyncio.sleep(0.05)
        assert not task.done(), "cancellation crossed an in-flight publish"
        release_replace.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release_replace.set()

    assert _artifact_files(tmp_path / "ppt") == []


def test_render_slides_honors_pre_cancel_before_writing(tmp_path: Path) -> None:
    cancel_event = threading.Event()
    cancel_event.set()
    output_path = tmp_path / "cancelled.pptx"

    with pytest.raises(RuntimeError, match="cancel"):
        render_slides(
            [Slide(title="Never written")],
            output_path,
            cancel_event=cancel_event,
        )

    assert not output_path.exists()


@pytest.mark.asyncio
async def test_preview_cancellation_removes_published_artifact_without_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def cancel_preview(_path: Path) -> tuple[list[str], int]:
        raise asyncio.CancelledError

    monkeypatch.setattr("tutor.services.ppt.service.render_slides", _render_complete)
    monkeypatch.setattr(
        "tutor.agents.resource.ppt_generator._peek_pptx",
        cancel_preview,
    )
    service = PPTGenerationService(output_dir=tmp_path / "ppt")
    agent = PPTGeneratorAgent(ppt_service=service)
    stream = StreamBus()
    queue = stream.subscribe()

    with pytest.raises(asyncio.CancelledError):
        await agent.process(
            topic="Preview cancellation",
            source_content="# Preview cancellation\n\n## One\ncontent",
            stream=stream,
        )

    assert _artifact_files(tmp_path / "ppt") == []
    assert queue.empty()


@pytest.mark.asyncio
async def test_observation_cancellation_removes_published_artifact_without_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CancellingObservationStream(StreamBus):
        async def observation(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            raise asyncio.CancelledError

    monkeypatch.setattr("tutor.services.ppt.service.render_slides", _render_complete)
    monkeypatch.setattr(
        "tutor.agents.resource.ppt_generator._peek_pptx",
        lambda _path: (["Intro"], 1),
    )
    service = PPTGenerationService(output_dir=tmp_path / "ppt")
    agent = PPTGeneratorAgent(ppt_service=service)
    stream = CancellingObservationStream()
    queue = stream.subscribe()

    with pytest.raises(asyncio.CancelledError):
        await agent.process(
            topic="Observation cancellation",
            source_content="# Observation cancellation\n\n## One\ncontent",
            stream=stream,
        )

    assert _artifact_files(tmp_path / "ppt") == []
    assert queue.empty()


@pytest.mark.asyncio
async def test_successful_ppt_atomically_publishes_from_private_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered_paths: list[Path] = []

    def render(
        slides: Any,
        output_path: Path,
        *,
        title: str = "",
        cancel_event: threading.Event | None = None,
    ) -> Path:
        del slides, title
        assert cancel_event is not None and not cancel_event.is_set()
        rendered_paths.append(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"complete-pptx")
        return output_path

    monkeypatch.setattr("tutor.services.ppt.service.render_slides", render)
    monkeypatch.setattr(
        "tutor.agents.resource.ppt_generator._peek_pptx",
        lambda path: (["Intro"], 1),
    )
    service = PPTGenerationService(output_dir=tmp_path / "ppt")
    agent = PPTGeneratorAgent(ppt_service=service)

    resource = await agent.process(
        topic="Success",
        source_content="# Success\n\n## One\ncontent",
    )
    final_path = Path(resource.format_specific["pptx_path"])

    assert final_path.is_file()
    assert final_path.read_bytes() == b"complete-pptx"
    assert rendered_paths and rendered_paths[0] != final_path
    assert ".tmp" in rendered_paths[0].parts
    assert not rendered_paths[0].exists()
    assert _artifact_files(tmp_path / "ppt") == [final_path]
