"""Health check + capabilities introspection endpoints."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from tutor import __version__

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Liveness + readiness probe.

    Returns basic environment info and the set of registered capabilities/tools.
    """
    settings = request.app.state.settings
    matplotlib_runtime = await asyncio.to_thread(
        _matplotlib_runtime_diagnostics,
        settings,
    )
    return {
        "status": "ok",
        "readiness": (
            "ready" if matplotlib_runtime["status"] == "ok" else "degraded"
        ),
        "version": __version__,
        "python": sys.version.split()[0],
        "runtime": {
            "execution_python": _resolve_diagnostic_interpreter(settings),
            "matplotlib": matplotlib_runtime,
        },
    }


def _resolve_diagnostic_interpreter(settings: Any) -> str:
    """Resolve the configured executable without falling back after failure."""
    configured = str(getattr(settings, "execution_python", "") or sys.executable)
    located = shutil.which(configured)
    return str(Path(located or configured).expanduser().resolve())


def _matplotlib_runtime_diagnostics(settings: Any) -> dict[str, Any]:
    """Probe Matplotlib in the configured child interpreter.

    Only stable codes cross the HTTP boundary on failure; exception messages,
    subprocess stderr and tracebacks stay private. The configured executable
    and cache directory are deliberately returned as operator diagnostics.
    """
    cache_dir = (Path(settings.data_dir) / "cache" / "matplotlib").resolve()
    result: dict[str, Any] = {
        "status": "unavailable",
        "version": None,
        "backend": None,
        "cache_dir": str(cache_dir),
        "writable": False,
        "error_code": "MATPLOTLIB_RUNTIME_UNAVAILABLE",
    }
    marker = cache_dir / f".health-write-{uuid.uuid4().hex}"
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text("ok", encoding="utf-8")
        marker.unlink()
        result["writable"] = True
    except OSError:
        return result

    interpreter = _resolve_diagnostic_interpreter(settings)
    probe = (
        "import json, matplotlib\n"
        "print(json.dumps({"
        "'version': matplotlib.__version__, "
        "'backend': matplotlib.get_backend(), "
        "'cache_dir': matplotlib.get_cachedir()"
        "}))\n"
    )
    env = os.environ.copy()
    env["MPLBACKEND"] = "Agg"
    env["MPLCONFIGDIR"] = str(cache_dir)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            [interpreter, "-c", probe],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=env,
        )
        if completed.returncode != 0:
            return result
        line = (completed.stdout or "").strip().splitlines()[-1]
        payload = json.loads(line)
        result["version"] = str(payload["version"])
        result["backend"] = str(payload["backend"])
        if (
            result["backend"].lower() != "agg"
            or Path(str(payload["cache_dir"])).resolve() != cache_dir
        ):
            result["error_code"] = "MATPLOTLIB_RUNTIME_MISCONFIGURED"
            return result
        result.update(
            {
                "status": "ok",
                "version": str(payload["version"]),
                "backend": str(payload["backend"]),
                "error_code": None,
            }
        )
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError):
        return result
    except Exception:  # noqa: BLE001 - never expose unexpected host details
        return result
    return result


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
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        first = (out.stdout or out.stderr).splitlines()
        return first[0] if first else "(no output)"
    except Exception as exc:  # noqa: BLE001
        return f"{exe}: error ({exc})"


__all__ = ["router"]
