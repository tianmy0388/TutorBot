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
import shutil
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from tutor.services.config.settings import get_settings
from tutor.services.manim_render.code_retry import CodeRetry, RetryResult
from tutor.services.manim_render.executor import (
    ManimExecutor,
    ManimRenderResult,
    RenderStatus,
)
from tutor.services.manim_render.static_guard import StaticGuard, StaticGuardResult


@dataclass
class RenderedVideo:
    """Final outcome of :meth:`ManimRenderService.render`."""

    success: bool
    code: str
    video_path: Optional[Path] = None
    duration_seconds: float = 0.0
    attempts: int = 0
    error: str = ""
    static_guard: Optional[StaticGuardResult] = None
    retry_result: Optional[RetryResult] = None
    final_render: Optional[ManimRenderResult] = None
    public_url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "video_path": str(self.video_path) if self.video_path else None,
            "public_url": self.public_url,
            "duration_seconds": round(self.duration_seconds, 2),
            "attempts": self.attempts,
            "error": self.error[:500],
            "static_guard_passed": self.static_guard.passed if self.static_guard else None,
            "render_status": (
                self.final_render.status.value if self.final_render else None
            ),
        }


class ManimRenderService:
    """End-to-end Manim rendering with validation + retry."""

    def __init__(
        self,
        *,
        static_guard: Optional[StaticGuard] = None,
        executor: Optional[ManimExecutor] = None,
        code_retry: Optional[CodeRetry] = None,
        public_dir: Optional[Path] = None,
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
        self.public_dir = Path(public_dir) if public_dir else Path("./data/manim_videos")
        self.public_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        return self.executor.is_available()

    def validate(self, code: str) -> StaticGuardResult:
        """Run pre-render checks (synchronous, no rendering)."""
        return self.static_guard.check(code)

    async def render(
        self,
        *,
        code: str,
        scene_class: str = "MainScene",
        job_id: Optional[str] = None,
    ) -> RenderedVideo:
        """Full pipeline: validate → render → (retry on failure) → publish."""
        # Stage 1: StaticGuard
        sg = self.static_guard.check(code)
        if not sg.passed:
            return RenderedVideo(
                success=False,
                code=code,
                attempts=0,
                error=f"static_guard_failed: {'; '.join(sg.errors[:3])}",
                static_guard=sg,
            )

        cleaned = sg.cleaned_code or code

        # Stage 2 + 3: render with retry
        render_fn, render_history = self._make_render_fn(scene_class, job_id)
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
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _make_render_fn(self, scene_class: str, job_id: Optional[str]):
        """Build the closure passed to CodeRetry — runs executor and tracks results.

        Returns ``(render_fn, results_holder)`` where ``render_fn`` is the
        ``async (snippet) -> (ok, error)`` and ``results_holder`` is a list
        that the closure appends the latest :class:`ManimRenderResult` to.
        """
        results: list[ManimRenderResult] = []

        async def _render_with_track(snippet: str) -> tuple[bool, str]:
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(
                None,
                lambda: self.executor.render(snippet, scene_class, job_id=job_id),
            )
            results.append(res)
            ok = res.status == RenderStatus.SUCCESS and res.video_path is not None
            err = res.error_message or (res.stderr[-500:] if res.stderr else "render failed")
            return ok, err

        return _render_with_track, results

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


_service: Optional[ManimRenderService] = None
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
