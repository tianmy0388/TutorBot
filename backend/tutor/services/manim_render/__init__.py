"""Manim rendering service.

Pipeline (inspired by ManimCat):

1. **StaticGuard**  — ``py_compile`` syntax check; abort if syntax errors.
2. **CodeRetry**    — on render failure, call LLM to produce SEARCH/REPLACE
                     patches (up to N attempts).
3. **ManimExecutor**— subprocess call to ``manim``; produces MP4.
4. **ManimRenderService** — high-level facade combining the above.

Design follows ManimCat's two-stage AI + retry loop. We port it from
TypeScript to Python and simplify for our MVP use case.
"""

from tutor.services.manim_render.code_retry import CodeRetry, RetryResult
from tutor.services.manim_render.executor import (
    ManimExecutor,
    ManimRenderResult,
    RenderFailure,
    RenderStatus,
)
from tutor.services.manim_render.service import (
    ManimRenderService,
    RenderedVideo,
    get_manim_render_service,
    reset_manim_render_service,
)
from tutor.services.manim_render.static_guard import StaticGuard, StaticGuardResult

__all__ = [
    "CodeRetry",
    "ManimExecutor",
    "ManimRenderResult",
    "ManimRenderService",
    "RenderFailure",
    "RenderStatus",
    "RenderedVideo",
    "RetryResult",
    "StaticGuard",
    "StaticGuardResult",
    "get_manim_render_service",
    "reset_manim_render_service",
]
