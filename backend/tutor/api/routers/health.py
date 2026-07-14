"""Health check + capabilities introspection endpoints."""

from __future__ import annotations

import shutil
import sys
from typing import Any

from fastapi import APIRouter, Request

from tutor import __version__

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, Any]:
    """Liveness + readiness probe.

    Returns basic environment info and the set of registered capabilities/tools.
    """
    return {
        "status": "ok",
        "version": __version__,
        "python": sys.version.split()[0],
    }


@router.get("/capabilities")
async def capabilities(request: Request) -> dict[str, Any]:
    """List all registered capabilities (with manifests) and tools."""
    caps = request.app.state.capabilities
    tools = request.app.state.tools
    return {
        "capabilities": caps.get_manifests(),
        "tools": [
            {"name": t.name, "description": t.description}
            for t in [
                tools.get(n) for n in tools.list_tools()
            ]
            if t is not None
        ],
    }


@router.get("/info")
async def info(request: Request) -> dict[str, Any]:
    """Server-side runtime info (LLM, RAG, Manim status).

    2026-06-21 plan: the response now includes the resolved data_dir
    and the canonical DB path so operators can confirm at a glance
    that the process is reading/writing the same database across
    restarts regardless of the cwd the backend was launched from.
    """
    settings = request.app.state.settings
    data_dir = settings.data_dir
    db_path = data_dir / "tutor.db"
    return {
        "version": __version__,
        "env": settings.env,
        "language": settings.language,
        "data_dir": str(data_dir),
        "db_path": str(db_path),
        "llm": {
            "provider": settings.llm_provider,
            "model": settings.llm_model,
            "base_url": settings.llm_base_url,
            "has_api_key": bool(settings.llm_api_key),
        },
        "rag": {
            "provider": settings.rag_provider,
            "kb_default": settings.kb_default,
        },
        "manim": {
            "enabled": settings.manim_enabled,
            "quality": settings.manim_quality,
        },
        "execution_python": getattr(settings, "execution_python", "") or sys.executable,
    }


@router.get("/system-check")
async def system_check() -> dict[str, Any]:
    """Verify external dependencies (manim, ffmpeg, ...)."""
    tools = {
        "python": sys.version.split()[0],
        "manim": _safe_check(["manim", "--version"]),
        "ffmpeg": _safe_check(["ffmpeg", "-version"]),
    }
    return {"tools": tools}


def _safe_check(cmd: list[str]) -> str:
    """Run a command, return its version line or an error message."""
    exe = cmd[0]
    if shutil.which(exe) is None:
        return f"{exe}: not installed"
    try:
        import subprocess

        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        first = (out.stdout or out.stderr).splitlines()
        return first[0] if first else "(no output)"
    except Exception as exc:  # noqa: BLE001
        return f"{exe}: error ({exc})"


__all__ = ["router"]
