"""Tests for :mod:`tutor.services.manim_render.executor`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tutor.services.manim_render.executor import (
    ManimExecutor,
    ManimRenderResult,
    RenderStatus,
)


SIMPLE_VALID_CODE = '''from manim import *


class HelloScene(Scene):
    def construct(self):
        t = Text("Hello")
        self.play(Write(t))
        self.wait(1)
'''


# Tests that need manim installed
requires_manim = pytest.mark.skipif(
    shutil.which("manim") is None,
    reason="manim not installed",
)


# ---------------------------------------------------------------------------
# Basic (no manim needed)
# ---------------------------------------------------------------------------


def test_quality_validation():
    with pytest.raises(ValueError):
        ManimExecutor(quality="z")  # type: ignore[arg-type]


def test_directories_created(tmp_path):
    exe = ManimExecutor(
        output_dir=tmp_path / "out",
        temp_dir=tmp_path / "tmp",
        timeout_seconds=10,
    )
    assert exe.output_dir.exists()
    assert exe.temp_dir.exists()


def test_render_returns_not_found_when_no_manim(tmp_path, monkeypatch):
    """If manim binary is missing, executor returns NOT_FOUND gracefully."""
    # Force resolution to fail
    monkeypatch.setattr(
        "shutil.which", lambda x: None
    )
    exe = ManimExecutor(
        output_dir=tmp_path / "out",
        temp_dir=tmp_path / "tmp",
        timeout_seconds=10,
    )
    result = exe.render(SIMPLE_VALID_CODE, "HelloScene")
    assert result.status == RenderStatus.NOT_FOUND
    assert "manim" in result.error_message.lower() or "not found" in result.error_message.lower()


def test_active_jobs_empty_initially(tmp_path):
    exe = ManimExecutor(
        output_dir=tmp_path / "out",
        temp_dir=tmp_path / "tmp",
        timeout_seconds=10,
    )
    assert exe.active_jobs() == []


def test_cancel_returns_false_for_unknown_job(tmp_path):
    exe = ManimExecutor(
        output_dir=tmp_path / "out",
        temp_dir=tmp_path / "tmp",
        timeout_seconds=10,
    )
    assert exe.cancel("does_not_exist") is False


# ---------------------------------------------------------------------------
# Real manim rendering
# ---------------------------------------------------------------------------


@requires_manim
def test_render_simple_scene_succeeds(tmp_path):
    """End-to-end: write code, run manim, find mp4."""
    exe = ManimExecutor(
        quality="l",
        output_dir=tmp_path / "out",
        temp_dir=tmp_path / "tmp",
        timeout_seconds=120,
    )
    result = exe.render(SIMPLE_VALID_CODE, "HelloScene")
    assert result.status == RenderStatus.SUCCESS, (
        f"render failed: stderr={result.stderr[:500]}"
    )
    assert result.video_path is not None
    assert result.video_path.exists()
    assert result.video_path.stat().st_size > 0
    assert result.duration_seconds > 0


@requires_manim
def test_render_unknown_scene_class_fails(tmp_path):
    """If scene class doesn't exist, manim should fail."""
    exe = ManimExecutor(
        quality="l",
        output_dir=tmp_path / "out",
        temp_dir=tmp_path / "tmp",
        timeout_seconds=60,
    )
    result = exe.render(SIMPLE_VALID_CODE, "NoSuchScene")
    assert result.status == RenderStatus.FAILED


@requires_manim
def test_render_syntax_error_in_code_fails(tmp_path):
    exe = ManimExecutor(
        quality="l",
        output_dir=tmp_path / "out",
        temp_dir=tmp_path / "tmp",
        timeout_seconds=60,
    )
    result = exe.render(
        "from manim import *\nclass Bad(Scene:\n  pass\n",  # bad syntax
        "Bad",
    )
    assert result.status == RenderStatus.FAILED
