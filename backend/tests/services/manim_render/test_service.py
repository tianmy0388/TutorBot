"""Tests for :mod:`tutor.services.manim_render.service`."""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from tutor.services.llm.base import LLMResponse
from tutor.services.manim_render.code_retry import CodeRetry
from tutor.services.manim_render.executor import ManimExecutor, ManimRenderResult, RenderStatus
from tutor.services.manim_render.service import ManimRenderService
from tutor.services.manim_render.static_guard import StaticGuard

VALID_CODE = '''from manim import *


class HelloScene(Scene):
    def construct(self):
        t = Text("Hello")
        self.play(Write(t))
        self.wait(1)
'''


requires_manim = pytest.mark.skipif(
    shutil.which("manim") is None and importlib.util.find_spec("manim") is None,
    reason="manim not installed",
)


def _mock_executor_success(tmp_path: Path):
    """Build a mock executor that always succeeds."""
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"FAKE_MP4_DATA")

    executor = MagicMock(spec=ManimExecutor)
    executor.quality = "l"
    executor.output_dir = tmp_path / "out"
    executor.temp_dir = tmp_path / "tmp"
    executor.is_available.return_value = True
    executor.render.return_value = ManimRenderResult(
        status=RenderStatus.SUCCESS,
        video_path=fake_video,
        exit_code=0,
        duration_seconds=10.0,
    )
    return executor


def _mock_executor_failure():
    executor = MagicMock(spec=ManimExecutor)
    executor.is_available.return_value = True
    executor.render.return_value = ManimRenderResult(
        status=RenderStatus.FAILED,
        stderr="NameError: name 'undefined' is not defined",
        error_message="render failed: undefined",
    )
    return executor


def _mock_code_retry_with_patch(patch_search: str, patch_replace: str):
    """Mock retry that returns code with one patch applied."""
    cr = MagicMock(spec=CodeRetry)

    async def fix(*, original_code, render_fn):
        from tutor.services.manim_render.code_retry import RetryResult

        # Apply the patch
        patched = original_code.replace(patch_search, patch_replace, 1)
        return RetryResult(
            success=True,
            code=patched,
            attempts_used=2,
            history=[
                {"attempt": 1, "ok": False, "error": "fail"},
                {"attempt": 2, "ok": True},
            ],
        )

    cr.fix_until_renderable = fix
    return cr


# ---------------------------------------------------------------------------
# Static validation
# ---------------------------------------------------------------------------


def test_validate_passes_for_good_code():
    svc = ManimRenderService(public_dir=Path("./data/test_manim"))
    result = svc.validate(VALID_CODE)
    assert result.passed is True


def test_validate_fails_for_bad_code():
    svc = ManimRenderService(public_dir=Path("./data/test_manim"))
    result = svc.validate("def broken(:\n  pass\n")
    assert result.passed is False


# ---------------------------------------------------------------------------
# End-to-end with mocks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_happy_path(tmp_path):
    """StaticGuard passes + Executor succeeds → RenderedVideo with success=True."""
    executor = _mock_executor_success(tmp_path)
    svc = ManimRenderService(
        static_guard=StaticGuard(),
        executor=executor,
        code_retry=CodeRetry(llm=_mock_llm_no_op(), max_attempts=1),
        public_dir=tmp_path / "public",
    )

    result = await svc.render(code=VALID_CODE, scene_class="HelloScene")
    assert result.success is True
    assert result.video_path is not None
    assert result.video_path.exists()
    assert result.public_url != ""
    assert result.attempts == 1
    assert result.static_guard.passed


@pytest.mark.asyncio
async def test_render_serializes_a_portable_artifact_key(tmp_path, monkeypatch):
    from tutor.services.config.settings import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path, raising=False)
    executor = _mock_executor_success(tmp_path)
    svc = ManimRenderService(
        static_guard=StaticGuard(),
        executor=executor,
        code_retry=CodeRetry(llm=_mock_llm_no_op(), max_attempts=1),
        public_dir=tmp_path / "manim_videos",
    )

    result = await svc.render(code=VALID_CODE, scene_class="HelloScene")

    serialized = result.to_dict()
    digest = hashlib.sha256(b"FAKE_MP4_DATA").hexdigest()
    assert serialized["artifact_key"] == f"manim_videos/{digest}.mp4"
    assert "video_path" not in serialized


@pytest.mark.asyncio
async def test_render_static_guard_failure_short_circuits(tmp_path):
    """Bad code → never calls executor."""
    executor = _mock_executor_success(tmp_path)
    svc = ManimRenderService(
        static_guard=StaticGuard(),
        executor=executor,
        code_retry=CodeRetry(llm=_mock_llm_no_op(), max_attempts=1),
        public_dir=tmp_path / "public",
    )

    result = await svc.render(code="def broken(:\n  pass\n", scene_class="X")
    assert result.success is False
    assert "static_guard" in result.error
    executor.render.assert_not_called()


@pytest.mark.asyncio
async def test_initial_render_does_not_call_llm_patch_retry(tmp_path):
    """A runtime failure is terminal until the user requests regeneration."""
    executor = _mock_executor_failure()
    executor.temp_dir = tmp_path / "render-workdir"

    class RecordingLLM:
        model = "mock"
        default_temperature = 0.5
        default_max_tokens = 2048

        def __init__(self):
            self.calls = []

        async def call(self, request):
            self.calls.append(request)
            return LLMResponse(
                content='{"patches": [{"search": "Hello", "replace": "Hi"}]}',
                model="mock",
            )

    llm = RecordingLLM()
    service = ManimRenderService(
        static_guard=StaticGuard(),
        executor=executor,
        code_retry=CodeRetry(llm=llm, max_attempts=4),
        public_dir=tmp_path / "public",
    )

    result = await service.render(code=VALID_CODE, scene_class="HelloScene")

    assert result.success is False
    assert executor.render.call_count == 1
    assert llm.calls == []


@pytest.mark.asyncio
async def test_render_manim_not_available(tmp_path):
    executor = MagicMock(spec=ManimExecutor)
    executor.is_available.return_value = False
    svc = ManimRenderService(
        static_guard=StaticGuard(),
        executor=executor,
        code_retry=CodeRetry(llm=_mock_llm_no_op(), max_attempts=1),
        public_dir=tmp_path / "public",
    )
    result = await svc.render(code=VALID_CODE, scene_class="HelloScene")
    # Static guard would fail because we don't have manim to test, but here
    # static guard runs first → it may pass; the executor check is internal.
    # If static_guard passes, we attempt render, executor returns NOT_FOUND
    # → retry gets stuck → returns failure.
    if result.static_guard.passed:
        assert result.success is False


@pytest.mark.asyncio
async def test_render_failure_keeps_last_120_lines_and_complete_log_artifact(
    tmp_path,
    monkeypatch,
):
    from tutor.services.artifacts import resolve_artifact_key
    from tutor.services.config.settings import get_settings

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)
    lines = [f"trace line {index:03d}" for index in range(150)]
    stderr = "\n".join(lines)
    executor = MagicMock(spec=ManimExecutor)
    executor.is_available.return_value = True
    executor.temp_dir = tmp_path / "render-workdir"
    executor.temp_dir.mkdir()
    executor.render.return_value = ManimRenderResult(
        status=RenderStatus.FAILED,
        stdout="complete stdout Ω",
        stderr=stderr,
        exit_code=1,
        error_message="unsafe C:\\private\\render.py detail",
    )
    svc = ManimRenderService(
        static_guard=StaticGuard(),
        executor=executor,
        code_retry=CodeRetry(llm=_mock_llm_no_op(), max_attempts=1),
        public_dir=data_dir / "manim_videos",
    )

    result = await svc.render(
        code=VALID_CODE,
        scene_class="HelloScene",
        job_id="child-video-failure",
    )

    assert result.success is False
    assert result.failure.error_code == "process_exit"
    assert result.failure.traceback_tail == tuple(lines[-120:])
    assert len(result.failure.summary) <= 200
    assert "C:\\private" not in result.failure.summary
    assert result.failure.log_artifact_key
    log_path = resolve_artifact_key(result.failure.log_artifact_key, data_dir)
    log_text = log_path.read_text(encoding="utf-8")
    assert "complete stdout Ω" in log_text
    assert lines[0] in log_text
    assert lines[-1] in log_text
    assert result.to_dict()["failure"]["traceback_tail"] == lines[-120:]


@pytest.mark.asyncio
async def test_publish_is_content_addressed_and_never_overwrites_prior_video(tmp_path):
    first_source = tmp_path / "first" / "MainScene.mp4"
    second_source = tmp_path / "second" / "MainScene.mp4"
    first_source.parent.mkdir()
    second_source.parent.mkdir()
    first_source.write_bytes(b"first-user-video")
    second_source.write_bytes(b"second-user-video")
    executor = MagicMock(spec=ManimExecutor)
    executor.is_available.return_value = True
    executor.temp_dir = tmp_path / "work"
    executor.render.side_effect = [
        ManimRenderResult(status=RenderStatus.SUCCESS, video_path=first_source),
        ManimRenderResult(status=RenderStatus.SUCCESS, video_path=second_source),
    ]
    service = ManimRenderService(
        executor=executor,
        public_dir=tmp_path / "public",
    )

    first = await service.render(
        code=VALID_CODE,
        scene_class="HelloScene",
        job_id="owner-a-resource-a",
    )
    first_bytes = first.video_path.read_bytes()
    second = await service.render(
        code=VALID_CODE,
        scene_class="HelloScene",
        job_id="owner-b-resource-b",
    )

    assert first.success is True and second.success is True
    assert first.video_path != second.video_path
    assert first.public_url != second.public_url
    assert first.video_path.read_bytes() == first_bytes == b"first-user-video"
    assert second.video_path.read_bytes() == b"second-user-video"


@pytest.mark.asyncio
async def test_publish_copy_failure_returns_structured_terminal_failure(
    tmp_path,
    monkeypatch,
):
    executor = _mock_executor_success(tmp_path)
    service = ManimRenderService(
        executor=executor,
        public_dir=tmp_path / "public",
    )

    def fail_copy(*args, **kwargs):
        raise OSError("provider-token=private-value C:\\private\\video.mp4")

    monkeypatch.setattr("tutor.services.manim_render.service.shutil.copy2", fail_copy)
    result = await service.render(
        code=VALID_CODE,
        scene_class="HelloScene",
        job_id="publish-failure-child",
    )

    assert result.success is False
    assert result.video_path is None
    assert result.public_url == ""
    assert result.failure.error_code == "publish_failed"
    assert result.failure.log_artifact_key
    assert "private-value" not in str(result.to_dict())
    assert "C:\\private" not in str(result.to_dict())


@pytest.mark.asyncio
async def test_empty_publish_result_is_failure_not_ready(tmp_path, monkeypatch):
    executor = _mock_executor_success(tmp_path)
    service = ManimRenderService(executor=executor, public_dir=tmp_path / "public")
    monkeypatch.setattr(service, "_publish", lambda *args: (None, ""))

    result = await service.render(code=VALID_CODE, scene_class="HelloScene")

    assert result.success is False
    assert result.failure.error_code == "publish_failed"
    assert result.video_path is None


@pytest.mark.asyncio
async def test_executor_exception_returns_bounded_structured_failure(tmp_path):
    executor = MagicMock(spec=ManimExecutor)
    executor.is_available.return_value = True
    executor.temp_dir = tmp_path / "work"
    executor.render.side_effect = RuntimeError(
        "provider-token=private-value at C:\\private\\scene.py"
    )
    service = ManimRenderService(executor=executor, public_dir=tmp_path / "public")

    result = await service.render(
        code=VALID_CODE,
        scene_class="HelloScene",
        job_id="executor-exception-child",
    )

    assert result.success is False
    assert result.attempts == 1
    assert result.final_render is None
    assert result.failure.error_code == "executor_exception"
    assert result.failure.log_artifact_key
    assert len(result.failure.summary) <= 200
    assert "private-value" not in str(result.to_dict())
    assert "C:\\private" not in str(result.to_dict())


@pytest.mark.asyncio
async def test_missing_render_history_returns_failure_instead_of_index_error(
    tmp_path,
    monkeypatch,
):
    executor = _mock_executor_failure()
    executor.temp_dir = tmp_path / "work"
    service = ManimRenderService(executor=executor, public_dir=tmp_path / "public")

    async def render_without_history(code):
        return False, "executor returned no result"

    monkeypatch.setattr(
        service,
        "_make_render_fn",
        lambda *args: (render_without_history, []),
    )
    result = await service.render(code=VALID_CODE, scene_class="HelloScene")

    assert result.success is False
    assert result.final_render is None
    assert result.failure.error_code == "missing_render_result"
    assert result.failure.log_artifact_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_llm_no_op():
    """LLM that returns empty patches (forces retry to give up early)."""
    from unittest.mock import MagicMock

    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        return LLMResponse(content="{}", model="mock")

    llm.call = call
    return llm


# ---------------------------------------------------------------------------
# Real manim end-to-end
# ---------------------------------------------------------------------------


@requires_manim
@pytest.mark.asyncio
async def test_real_render_full_pipeline(tmp_path):
    """Run the entire pipeline against real manim."""
    import os

    os.environ["TUTOR_MANIM_OUTPUT_DIR"] = str(tmp_path / "manim_out")
    os.environ["TUTOR_MANIM_TEMP_DIR"] = str(tmp_path / "manim_tmp")
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()

    svc = ManimRenderService(public_dir=tmp_path / "public")
    if not svc.is_available():
        pytest.skip("manim not available")

    result = await svc.render(
        code=VALID_CODE,
        scene_class="HelloScene",
    )
    assert result.success is True, (
        f"render failed: error={result.error[:300]}, "
        f"stderr={result.final_render.stderr[:300] if result.final_render else 'n/a'}"
    )
    assert result.video_path is not None
    assert result.video_path.exists()
    assert result.video_path.stat().st_size > 1000  # real video > 1KB
    assert result.duration_seconds > 0
    assert result.attempts == 1
