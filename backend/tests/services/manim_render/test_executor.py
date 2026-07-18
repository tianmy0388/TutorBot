"""Tests for :mod:`tutor.services.manim_render.executor`."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from tutor.services.manim_render.executor import (
    ManimExecutor,
    ManimRenderResult,
    RenderStatus,
    failure_for_render_result,
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
    shutil.which("manim") is None and importlib.util.find_spec("manim") is None,
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
    monkeypatch.setattr("shutil.which", lambda x: None)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    exe = ManimExecutor(
        output_dir=tmp_path / "out",
        temp_dir=tmp_path / "tmp",
        timeout_seconds=10,
    )
    result = exe.render(SIMPLE_VALID_CODE, "HelloScene")
    assert result.status == RenderStatus.NOT_FOUND
    assert "manim" in result.error_message.lower() or "not found" in result.error_message.lower()


def test_python_module_fallback_is_a_tokenized_command(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda x: None)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())

    exe = ManimExecutor(
        output_dir=tmp_path / "out",
        temp_dir=tmp_path / "tmp",
    )

    assert exe.is_available()
    assert exe.manim_executable == sys.executable
    assert exe._manim_command == [sys.executable, "-m", "manim"]


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


def test_timeout_preserves_complete_utf8_safe_streams(tmp_path, monkeypatch):
    class TimeoutProcess:
        returncode = None

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(
                    ["manim"],
                    timeout,
                    output=b"partial stdout \xff",
                    stderr=b"partial stderr \xfe",
                )
            self.returncode = -9
            return b"complete stdout \xff", b"root cause stderr \xfe"

        def send_signal(self, signal):
            self.returncode = -9

        def terminate(self):
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: TimeoutProcess())
    exe = ManimExecutor(
        manim_executable="manim",
        output_dir=tmp_path / "out",
        temp_dir=tmp_path / "tmp",
        timeout_seconds=1,
    )

    result = exe.render(SIMPLE_VALID_CODE, "HelloScene", job_id="timeout-job")

    assert result.status == RenderStatus.TIMEOUT
    assert result.stdout == "complete stdout �"
    assert result.stderr == "root cause stderr �"


def test_cancelled_process_maps_to_cancelled_with_captured_streams(
    tmp_path,
    monkeypatch,
):
    released = threading.Event()

    class BlockingProcess:
        returncode = None

        def communicate(self, timeout=None):
            assert released.wait(timeout=2)
            self.returncode = -9
            return "stdout before cancel", "stderr before cancel"

        def send_signal(self, signal):
            released.set()

        def terminate(self):
            released.set()

        def wait(self, timeout=None):
            released.set()
            self.returncode = -9
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: BlockingProcess())
    exe = ManimExecutor(
        manim_executable="manim",
        output_dir=tmp_path / "out",
        temp_dir=tmp_path / "tmp",
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            exe.render,
            SIMPLE_VALID_CODE,
            "HelloScene",
            job_id="cancel-job",
        )
        deadline = time.time() + 2
        while "cancel-job" not in exe.active_jobs() and time.time() < deadline:
            time.sleep(0.01)
        assert exe.cancel("cancel-job") is True
        result = future.result(timeout=3)

    assert result.status == RenderStatus.CANCELLED
    assert result.stdout == "stdout before cancel"
    assert result.stderr == "stderr before cancel"


def test_failure_projection_redacts_host_paths_from_diagnostic_tail():
    result = ManimRenderResult(
        status=RenderStatus.FAILED,
        exit_code=1,
        error_message=r'failed in C:\private\render\scene.py',
        stderr=(
            '  File "C:\\private\\render\\scene.py", line 9\n'
            "  File /tmp/tutor-render/generated.py, line 3\n"
            "ValueError: invalid color\n"
        ),
    )

    failure = failure_for_render_result(result)

    projection = "\n".join((failure.summary, *failure.traceback_tail))
    assert "C:\\private" not in projection
    assert "/tmp/tutor-render" not in projection
    assert "ValueError: invalid color" in projection


def test_internal_failure_projection_does_not_expose_exception_internals():
    result = ManimRenderResult(
        status=RenderStatus.FAILED,
        exit_code=-1,
        error_message="unexpected error: provider-token=private-value",
    )

    failure = failure_for_render_result(result)

    assert failure.error_code == "internal_error"
    assert failure.summary == "Video rendering failed internally"
    assert "private-value" not in str(failure.to_dict())


def test_public_tail_redacts_spaced_unc_posix_file_uri_and_credentials():
    diagnostics = "\n".join(
        (
            '  File "C:\\Program Files\\Tutor Bot\\scene.py", line 2',
            '  File "\\\\render-host\\private share\\scene.py", line 3',
            '  File "/opt/private project/scene.py", line 4',
            '  File "file:///C:/Users/Alice/secret scene.py", line 5',
            "api_key=sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
            "provider-token=private-value",
            "ValueError: 颜色无效 Ω",
        )
    )
    result = ManimRenderResult(
        status=RenderStatus.FAILED,
        exit_code=1,
        error_message="manim process failed",
        stderr=diagnostics,
    )

    failure = failure_for_render_result(result)

    projection = "\n".join(failure.traceback_tail)
    for forbidden in (
        "C:\\Program Files",
        "render-host",
        "/opt/private project",
        "file:///C:/Users",
        "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "private-value",
    ):
        assert forbidden not in projection
    assert "ValueError: 颜色无效 Ω" in projection


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
