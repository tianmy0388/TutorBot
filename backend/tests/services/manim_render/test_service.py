"""Tests for :mod:`tutor.services.manim_render.service`."""

from __future__ import annotations

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
    shutil.which("manim") is None,
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
async def test_render_retries_on_failure_then_succeeds(tmp_path):
    """First render fails, retry applies patch, second render succeeds."""
    # Build a real CodeRetry with mock LLM that produces a no-op patch
    # (the test patches the render fn to succeed on second call)
    from tutor.services.llm.base import LLMResponse
    from unittest.mock import MagicMock

    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    llm_responses = iter(
        [
            # First call: provide a patch
            '{"patches": [{"search": "Hello", "replace": "Hi", "explanation": "shorten"}]}',
        ]
    )

    async def call(req):
        return LLMResponse(content=next(llm_responses), model="mock")

    llm.call = call
    code_retry = CodeRetry(llm=llm, max_attempts=3)

    # Build an executor that fails first, succeeds second
    executor = MagicMock(spec=ManimExecutor)
    fake_video = tmp_path / "ok.mp4"
    fake_video.write_bytes(b"X")
    executor.is_available.return_value = True
    call_count = [0]

    def render_fn(code, scene_class, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return ManimRenderResult(
                status=RenderStatus.FAILED,
                stderr="render error 1",
                error_message="failed",
            )
        return ManimRenderResult(
            status=RenderStatus.SUCCESS,
            video_path=fake_video,
            exit_code=0,
            duration_seconds=5.0,
        )

    executor.render.side_effect = render_fn

    svc = ManimRenderService(
        static_guard=StaticGuard(),
        executor=executor,
        code_retry=code_retry,
        public_dir=tmp_path / "public",
    )

    result = await svc.render(code=VALID_CODE, scene_class="HelloScene")
    assert result.success is True
    assert result.attempts == 2  # first fail + retry success
    assert "Hi" in result.code  # patch was applied


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_llm_no_op():
    """LLM that returns empty patches (forces retry to give up early)."""
    from unittest.mock import MagicMock
    from tutor.services.llm.base import LLMResponse

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
    from tutor.services.config.settings import Settings, get_settings
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
