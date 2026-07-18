"""ManimRenderService — high-level facade combining StaticGuard + Executor + Retry.

Public API:

    from tutor.services.manim_render import get_manim_render_service

    svc = get_manim_render_service()
    result = await svc.render(manim_code="from manim import *...", scene_class="MainScene")
    print(result.video_path, result.attempts)

The service is async-friendly and provides:
- Synchronous validation (``validate``)
- Async full pipeline (``render``)
- Best-effort cleanup of intermediate files
- A :class:`RenderedVideo` result object that callers (e.g. background
  jobs) can persist.
"""

from __future__ import annotations

import asyncio
import hashlib
import shutil
import threading
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from tutor.services.artifacts import UnsafeArtifactKey, to_artifact_key
from tutor.services.config.settings import get_settings
from tutor.services.manim_render.code_retry import CodeRetry, RetryResult
from tutor.services.manim_render.executor import (
    ManimExecutor,
    ManimRenderResult,
    RenderFailure,
    RenderStatus,
    failure_for_render_result,
    safe_failure_summary,
    sanitize_public_diagnostic,
    tail_lines,
)
from tutor.services.manim_render.static_guard import StaticGuard, StaticGuardResult


@dataclass
class RenderedVideo:
    """Final outcome of :meth:`ManimRenderService.render`."""

    success: bool
    code: str
    video_path: Path | None = None
    duration_seconds: float = 0.0
    attempts: int = 0
    error: str = ""
    static_guard: StaticGuardResult | None = None
    retry_result: RetryResult | None = None
    final_render: ManimRenderResult | None = None
    public_url: str = ""
    failure: RenderFailure | None = None

    def to_dict(self) -> dict[str, Any]:
        artifact_key = None
        if self.video_path:
            try:
                artifact_key = to_artifact_key(
                    self.video_path, get_settings().data_dir
                )
            except UnsafeArtifactKey:
                # Custom render destinations remain an internal Path; never
                # leak an absolute host path into a persisted payload.
                artifact_key = None
        return {
            "success": self.success,
            "artifact_key": artifact_key,
            "public_url": self.public_url,
            "duration_seconds": round(self.duration_seconds, 2),
            "attempts": self.attempts,
            "error": self.error[:500],
            "static_guard_passed": self.static_guard.passed if self.static_guard else None,
            "render_status": (
                self.final_render.status.value if self.final_render else None
            ),
            "failure": self.failure.to_dict() if self.failure else None,
        }


class ManimRenderService:
    """End-to-end Manim rendering with validation + retry."""

    def __init__(
        self,
        *,
        static_guard: StaticGuard | None = None,
        executor: ManimExecutor | None = None,
        code_retry: CodeRetry | None = None,
        public_dir: Path | None = None,
    ) -> None:
        settings = get_settings()
        self.static_guard = static_guard or StaticGuard()
        self.executor = executor or ManimExecutor(
            quality=settings.manim_quality,
            output_dir=settings.manim_output_dir,
            temp_dir=settings.manim_temp_dir,
            timeout_seconds=settings.manim_timeout,
        )
        self.code_retry = code_retry or CodeRetry(
            max_attempts=settings.code_retry_max_attempts
        )
        # **2026-07-09 fix (ada95ede trace):** the public_dir default
        # used to be ``Path("./data/manim_videos")`` which is *relative
        # to the process cwd*. When the backend started under one cwd
        # and FastAPI mounted under another, the URL
        # ``/static/manim/MainScene.mp4`` 404'd because the served
        # directory and the published file landed in different places.
        # Anchor the default to ``settings.data_dir`` (which itself is
        # resolved to an absolute path under the repo root by the
        # Settings validator) so the same directory is used for both
        # ``_publish`` and the FastAPI ``StaticFiles`` mount wired up
        # in ``tutor/api/main.py``.
        if public_dir:
            self.public_dir = Path(public_dir)
        else:
            self.public_dir = settings.data_dir / "manim_videos"
        self.public_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return self.executor.is_available()

    def validate(
        self,
        code: str,
        *,
        workdir: Path | None = None,
    ) -> StaticGuardResult:
        """Run pre-render checks (synchronous, no rendering)."""
        return self.static_guard.check(code, workdir=workdir)

    async def render(
        self,
        *,
        code: str,
        scene_class: str = "MainScene",
        job_id: str | None = None,
    ) -> RenderedVideo:
        """Full pipeline: validate → render → (retry on failure) → publish."""
        # Stage 1: StaticGuard
        invocation_id = job_id or uuid.uuid4().hex
        configured_workdir = getattr(self.executor, "temp_dir", None)
        render_workdir = Path(
            configured_workdir
            if isinstance(configured_workdir, (str, Path))
            else get_settings().manim_temp_dir
        )
        render_workdir.mkdir(parents=True, exist_ok=True)
        sg = self.static_guard.check(code, workdir=render_workdir)
        if not sg.passed:
            log_key = self._write_log_artifact(
                invocation_id,
                attempt_label="preflight",
                stdout="",
                stderr="\n".join(sg.errors),
            )
            failure = RenderFailure(
                error_code=sg.error_code or "preflight_failed",
                summary=safe_failure_summary(
                    sg.summary,
                    fallback="Manim source failed preflight checks",
                ),
                traceback_tail=tail_lines("\n".join(sg.errors)),
                log_artifact_key=log_key,
            )
            return RenderedVideo(
                success=False,
                code=code,
                attempts=0,
                error=f"static_guard_failed: {failure.summary}",
                static_guard=sg,
                failure=failure,
            )

        if not self.executor.is_available():
            log_key = self._write_log_artifact(
                invocation_id,
                attempt_label="runtime-preflight",
                stdout="",
                stderr="Manim runtime is unavailable",
            )
            failure = RenderFailure(
                error_code="manim_not_found",
                summary="Manim runtime is unavailable",
                traceback_tail=("Manim runtime is unavailable",),
                log_artifact_key=log_key,
            )
            return RenderedVideo(
                success=False,
                code=sg.cleaned_code or code,
                attempts=0,
                error=failure.summary,
                static_guard=sg,
                failure=failure,
            )

        cleaned = sg.cleaned_code or code

        # Stage 2 + 3: render with retry
        render_fn, render_history = self._make_render_fn(
            scene_class,
            invocation_id,
        )
        retry_result = await self.code_retry.fix_until_renderable(
            original_code=cleaned,
            render_fn=render_fn,
        )

        # Last successful render result (if any)
        final_render = None
        if retry_result.success and render_history:
            final_render = render_history[-1]
        elif render_history:
            final_render = render_history[-1]  # show last attempt even if failed

        # Stage 4: publish (copy to public dir)
        video_path = None
        public_url = ""
        if retry_result.success and final_render and final_render.video_path:
            video_path, public_url = self._publish(
                final_render.video_path, scene_class
            )

        return RenderedVideo(
            success=retry_result.success,
            code=retry_result.code,
            video_path=video_path,
            duration_seconds=final_render.duration_seconds if final_render else 0.0,
            attempts=retry_result.attempts_used,
            error=retry_result.final_error,
            static_guard=sg,
            retry_result=retry_result,
            final_render=final_render,
            public_url=public_url,
            failure=retry_result.failure,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _make_render_fn(self, scene_class: str, job_id: str):
        """Build the closure passed to CodeRetry — runs executor and tracks results.

        Returns ``(render_fn, results_holder)`` where ``render_fn`` is the
        ``async (snippet) -> (ok, error)`` and ``results_holder`` is a list
        that the closure appends the latest :class:`ManimRenderResult` to.
        """
        results: list[ManimRenderResult] = []
        attempt = 0

        async def _render_with_track(
            snippet: str,
        ) -> tuple[bool, RenderFailure | str]:
            nonlocal attempt
            attempt += 1
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(
                None,
                lambda: self.executor.render(
                    snippet,
                    scene_class,
                    # Manim repeats the source stem under videos/... and
                    # partial_movie_files. Keep this executor-local id short
                    # enough for Windows' legacy MAX_PATH boundary while the
                    # full durable child id remains in the canonical log key.
                    job_id=(
                        f"{hashlib.sha256(job_id.encode('utf-8')).hexdigest()[:10]}"
                        f"a{attempt}"
                    ),
                ),
            )
            results.append(res)
            ok = res.status == RenderStatus.SUCCESS and res.video_path is not None
            if ok:
                return True, ""
            log_key = self._write_log_artifact(
                job_id,
                attempt_label=f"attempt-{attempt:02d}",
                stdout=res.stdout,
                stderr=res.stderr,
            )
            failure = failure_for_render_result(
                res,
                log_artifact_key=log_key,
            )
            res.failure = failure
            return False, failure

        return _render_with_track, results

    @staticmethod
    def _write_current_exception_log_artifact(
        job_id: str,
        *,
        attempt_label: str,
        public_stderr: str,
    ) -> str:
        """Keep the active traceback only in the access-controlled operator log."""

        return ManimRenderService._write_log_artifact(
            job_id,
            attempt_label=attempt_label,
            stdout="",
            stderr=public_stderr,
            operator_stderr=traceback.format_exc(),
        )

    @staticmethod
    def _write_log_artifact(
        job_id: str,
        *,
        attempt_label: str,
        stdout: str,
        stderr: str,
        operator_stdout: str | None = None,
        operator_stderr: str | None = None,
    ) -> str:
        """Persist raw operator streams and a sanitized downloadable log."""
        data_dir = Path(get_settings().data_dir)
        safe_job_id = "".join(
            character for character in job_id if character.isalnum() or character in "-_"
        )[:96] or uuid.uuid4().hex
        filename = f"{attempt_label}.log"
        public_log = (
            "[stdout]\n"
            + (stdout or "")
            + "\n[stderr]\n"
            + (stderr or "")
        )
        raw_log = (
            "[stdout]\n"
            + (stdout if operator_stdout is None else operator_stdout)
            + "\n[stderr]\n"
            + (stderr if operator_stderr is None else operator_stderr)
        )
        operator_log_path = (
            data_dir / "operator_logs" / "manim" / safe_job_id / filename
        )
        operator_log_path.parent.mkdir(parents=True, exist_ok=True)
        operator_log_path.write_text(
            raw_log,
            encoding="utf-8",
            errors="replace",
        )
        log_path = data_dir / "manim_logs" / safe_job_id / filename
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            sanitize_public_diagnostic(public_log),
            encoding="utf-8",
            errors="replace",
        )
        return to_artifact_key(log_path, data_dir)

    def _publish(
        self,
        video_path: Path,
        scene_class: str,
    ) -> tuple[Path, str]:
        """Copy ``video_path`` to the public dir. Returns (new_path, url)."""
        dest = self.public_dir / video_path.name
        try:
            shutil.copy2(video_path, dest)
        except OSError as exc:
            logger.warning(f"Publish failed: {exc}")
            return video_path, ""
        # URL is relative; the FastAPI app would mount /static → public_dir in production
        return dest, f"/static/manim/{dest.name}"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


_service: ManimRenderService | None = None
_service_lock = threading.Lock()


def get_manim_render_service() -> ManimRenderService:
    """Return the singleton :class:`ManimRenderService`."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = ManimRenderService()
                logger.info(
                    f"ManimRenderService ready (manim={'available' if _service.is_available() else 'NOT FOUND'}, "
                    f"quality={_service.executor.quality})"
                )
    return _service


def reset_manim_render_service() -> None:
    """Clear the singleton (tests only)."""
    global _service
    _service = None


__all__ = ["ManimRenderService", "RenderedVideo", "get_manim_render_service", "reset_manim_render_service"]
