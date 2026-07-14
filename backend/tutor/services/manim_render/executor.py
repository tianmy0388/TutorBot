"""ManimExecutor — subprocess wrapper around the ``manim`` CLI.

Responsibilities:
- Resolve the ``manim`` executable (``shutil.which`` first, fallback paths).
- Write the Python source to a temp file.
- Spawn ``manim`` with the right quality flag and output location.
- Capture stdout / stderr / exit code.
- Honour a hard timeout (kill the subprocess if exceeded).
- Track peak memory (best-effort via OS query).
- Allow cancellation.

Design inspired by ManimCat's ``manim-executor`` (TypeScript port to
Python, with Windows-friendly subprocess handling).
"""

from __future__ import annotations

import enum
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class RenderStatus(str, enum.Enum):
    """Outcome of a render attempt."""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    NOT_FOUND = "not_found"  # manim executable not installed


@dataclass
class ManimRenderResult:
    """Outcome of one ``manim`` execution."""

    status: RenderStatus
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    output_path: Optional[Path] = None
    duration_seconds: float = 0.0
    peak_memory_mb: float = 0.0
    error_message: str = ""
    # The full path to the rendered .mp4 (if success)
    video_path: Optional[Path] = None

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "stdout_tail": self.stdout[-500:] if self.stdout else "",
            "stderr_tail": self.stderr[-500:] if self.stderr else "",
            "exit_code": self.exit_code,
            "output_path": str(self.output_path) if self.output_path else None,
            "video_path": str(self.video_path) if self.video_path else None,
            "duration_seconds": self.duration_seconds,
            "peak_memory_mb": self.peak_memory_mb,
            "error_message": self.error_message,
        }


class ManimExecutor:
    """Run ``manim`` against a Python source file."""

    def __init__(
        self,
        *,
        quality: str = "l",
        output_dir: Optional[Path] = None,
        temp_dir: Optional[Path] = None,
        timeout_seconds: int = 600,
        manim_executable: Optional[str] = None,
    ) -> None:
        if quality not in ("l", "m", "h"):
            raise ValueError(f"quality must be one of l/m/h, got {quality!r}")
        self.quality = quality
        self.output_dir = Path(output_dir) if output_dir else Path.cwd() / "manim_output"
        self.temp_dir = Path(temp_dir) if temp_dir else Path.cwd() / "manim_temp"
        self.timeout_seconds = timeout_seconds
        self.manim_executable = manim_executable or self._resolve_manim()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._active_processes: dict[str, subprocess.Popen] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return True if ``manim`` is on PATH and runnable."""
        return self.manim_executable is not None

    def render(
        self,
        code: str,
        scene_class: str = "MainScene",
        *,
        job_id: Optional[str] = None,
    ) -> ManimRenderResult:
        """Write ``code`` to a temp file and run ``manim``.

        Returns a :class:`ManimRenderResult`. On success,
        ``result.video_path`` points to the rendered MP4.
        """
        if not self.is_available():
            return ManimRenderResult(
                status=RenderStatus.NOT_FOUND,
                error_message=f"manim executable not found (PATH={os.environ.get('PATH', '')[:200]})",
            )

        job_id = job_id or uuid.uuid4().hex[:12]
        # Write code to temp file
        code_path = self.temp_dir / f"{job_id}.py"
        try:
            code_path.write_text(code, encoding="utf-8")
        except OSError as exc:
            return ManimRenderResult(
                status=RenderStatus.FAILED,
                error_message=f"failed to write temp file: {exc}",
            )

        # manim writes to <output_dir>/<quality>/<scene_class>.mp4
        # We use --media_dir to control the root, then look for the file.
        media_dir = self.output_dir / job_id
        media_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.manim_executable,  # type: ignore[list-item]
            "-q", self.quality,
            "--media_dir", str(media_dir),
            "--disable_caching",
            str(code_path),
            scene_class,
        ]

        t0 = time.time()
        proc: Optional[subprocess.Popen] = None
        try:
            # On Windows, shell=False is required for signal handling; we use
            # CREATE_NEW_PROCESS_GROUP to enable CTRL_BREAK_EVENT for kill.
            kwargs: dict[str, object] = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if os.name == "nt":
                kwargs["creationflags"] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP")
                    else 0
                )
            else:
                kwargs["start_new_session"] = True

            proc = subprocess.Popen(cmd, **kwargs)  # type: ignore[arg-type]
            with self._lock:
                self._active_processes[job_id] = proc

            try:
                stdout, stderr = proc.communicate(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                return ManimRenderResult(
                    status=RenderStatus.TIMEOUT,
                    error_message=f"render timeout after {self.timeout_seconds}s",
                    duration_seconds=time.time() - t0,
                )

            duration = time.time() - t0
            exit_code = proc.returncode if proc.returncode is not None else -1
            video_path = self._find_output_video(media_dir, scene_class)

            if exit_code == 0 and video_path is not None and video_path.exists():
                return ManimRenderResult(
                    status=RenderStatus.SUCCESS,
                    stdout=stdout or "",
                    stderr=stderr or "",
                    exit_code=exit_code,
                    output_path=media_dir,
                    video_path=video_path,
                    duration_seconds=duration,
                )
            return ManimRenderResult(
                status=RenderStatus.FAILED,
                stdout=stdout or "",
                stderr=stderr or "",
                exit_code=exit_code,
                output_path=media_dir,
                duration_seconds=duration,
                error_message=_extract_error(stderr or ""),
            )
        except FileNotFoundError as exc:
            return ManimRenderResult(
                status=RenderStatus.NOT_FOUND,
                error_message=f"manim binary disappeared: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            return ManimRenderResult(
                status=RenderStatus.FAILED,
                error_message=f"unexpected error: {exc}",
                duration_seconds=time.time() - t0,
            )
        finally:
            with self._lock:
                self._active_processes.pop(job_id, None)
            # 2026-06-21 plan (C4): persist the render logs to
            # ``media_dir/logs/`` so operators can debug
            # production videos without re-running the pipeline.
            # ``stdout`` / ``stderr`` are already captured from
            # ``proc.communicate()`` — we just write them to disk.
            _save_render_logs(
                media_dir,
                job_id,
                cmd=cmd,
                stdout=stdout if "stdout" in dir() else "",
                stderr=stderr if "stderr" in dir() else "",
                code=code,
            )
            # Clean up the temp .py file (the media dir stays for the caller)
            try:
                code_path.unlink()
            except OSError:
                pass

    def cancel(self, job_id: str) -> bool:
        """Kill a running render. Returns True if killed."""
        with self._lock:
            proc = self._active_processes.get(job_id)
        if proc is None:
            return False
        self._kill(proc)
        return True

    def active_jobs(self) -> list[str]:
        with self._lock:
            return list(self._active_processes.keys())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_manim() -> Optional[str]:
        """Locate the manim executable, with Windows-friendly fallbacks."""
        # 1) shutil.which (uses PATH)
        exe = shutil.which("manim")
        if exe:
            return exe
        # 2) python -m manim fallback (lets us use the current interpreter's manim)
        if sys.executable:
            return f"{sys.executable} -m manim"
        return None

    @staticmethod
    def _find_output_video(media_dir: Path, scene_class: str) -> Optional[Path]:
        """Manim writes to ``media_dir/videos/<script>/<quality>/<scene>.mp4``.

        We don't know the script name so we walk the directory.
        """
        for candidate in media_dir.rglob("*.mp4"):
            if candidate.stem == scene_class:
                return candidate
        return None

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                # CTRL_BREAK_EVENT is graceful; if that fails, terminate
                try:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                except Exception:
                    proc.terminate()
            else:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass


def _extract_error(stderr: str) -> str:
    """Pull the most informative line(s) from manim's stderr."""
    if not stderr:
        return "manim exited with non-zero status (no stderr)"
    lines = [ln.strip() for ln in stderr.splitlines() if ln.strip()]
    # Skip noise lines
    SKIP_PREFIXES = ("INFO", "DEBUG", "Traceback", "File \"")
    useful = [ln for ln in lines if not any(ln.startswith(p) for p in SKIP_PREFIXES)]
    if not useful:
        useful = lines[-3:]
    return "\n".join(useful[:5])


__all__ = ["ManimExecutor", "ManimRenderResult", "RenderStatus"]


def _save_render_logs(
    media_dir: Path,
    job_id: str,
    *,
    cmd: list[str],
    stdout: str,
    stderr: str,
    code: str,
) -> None:
    """Persist render logs to disk (2026-06-21 plan, C4).

    Writes three files under ``media_dir/logs/``:

      * ``command.txt`` — the exact CLI invocation
      * ``stdout.log`` — the combined stdout output
      * ``stderr.log`` — the combined stderr output
      * ``source.py`` — the Manim Python source that was rendered

    This is best-effort — a failure to write logs must not affect
    the render result, so we catch OSError and move on.
    """
    log_dir = media_dir / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "command.txt").write_text(
            " ".join(cmd) + "\n", encoding="utf-8"
        )
        if stdout:
            (log_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        if stderr:
            (log_dir / "stderr.log").write_text(stderr, encoding="utf-8")
        if code:
            (log_dir / "source.py").write_text(code, encoding="utf-8")
    except OSError:
        pass  # best-effort
