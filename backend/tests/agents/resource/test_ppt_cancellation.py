from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from tutor.agents.resource.ppt_generator import PPTGeneratorAgent
from tutor.core.stream_bus import StreamBus
from tutor.services.ppt.service import PPTGenerationService


class _BlockingRenderer:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.render_path: Path | None = None

    def __call__(
        self,
        slides: Any,
        output_path: Path,
        *,
        title: str = "",
    ) -> Path:
        del slides, title
        self.render_path = output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"private-partial-pptx")
        self.entered.set()
        try:
            assert self.release.wait(timeout=5)
            return output_path
        finally:
            self.finished.set()


async def _wait_thread_event(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 2)


def _artifact_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*") if path.is_file()]


@pytest.mark.asyncio
async def test_cancelled_ppt_worker_never_publishes_or_emits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = _BlockingRenderer()
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
    with pytest.raises(asyncio.CancelledError):
        await task
    renderer.release.set()
    await _wait_thread_event(renderer.finished)
    await asyncio.sleep(0)

    assert _artifact_files(tmp_path / "ppt") == []
    assert queue.empty()


@pytest.mark.asyncio
async def test_timed_out_ppt_worker_never_publishes_or_emits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = _BlockingRenderer()
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
    with pytest.raises(TimeoutError):
        await task
    renderer.release.set()
    await _wait_thread_event(renderer.finished)
    await asyncio.sleep(0)

    assert _artifact_files(tmp_path / "ppt") == []
    assert queue.empty()


@pytest.mark.asyncio
async def test_successful_ppt_atomically_publishes_from_private_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rendered_paths: list[Path] = []

    def render(slides: Any, output_path: Path, *, title: str = "") -> Path:
        del slides, title
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
