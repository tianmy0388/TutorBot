"""CodeSandboxAgent — generate runnable code examples with verification.

Pipeline role:
    Pedagogy output → CodeSandboxAgent → CodeResource

The agent asks the LLM to write a small, runnable example that
illustrates the concept. It then optionally runs the code in a
subprocess (with a strict timeout) to verify it executes without errors.

For MVP the sandbox is best-effort: we only run *short* code blocks
(< 200 lines, no network/file IO imports) and time out at 5 seconds.
Phase 5 will swap in a proper sandboxed runner (Docker / RestrictedPython).

2026-06-21 plan:
  - The runner uses ``Settings.execution_python`` (NOT the host
    ``sys.executable``); the dev launcher starts the backend through
    ``conda run -n tutor`` so the configured interpreter is the
    conda env that has matplotlib / manim / numpy.
  - Errors are reported with structured codes so the UI can show
    distinct copy:
        * ``RUNTIME_DEPENDENCY_MISSING`` — interpreter works but the
          code requires a package the env doesn't have
          (``ModuleNotFoundError``).
        * ``CODE_EXECUTION_FAILED``       — the LLM-generated code
          itself raised an exception.
  - The runner sets ``MPLBACKEND=Agg`` and one persistent
    ``data/cache/matplotlib`` config directory. Figure artifacts remain
    isolated in a unique per-run directory.
  - Any image / SVG / PDF files written to the scratch dir are
    attached as artifacts on the resource so the right pane can
    render them.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.agents.resource.manim_video import (
    _extract_first_python_block,
    _normalize_code_newlines,
)
from tutor.core.context import UnifiedContext
from tutor.core.redaction import public_failure, redact_sensitive, redact_text
from tutor.core.stream_bus import StreamBus
from tutor.services.artifacts import to_artifact_key
from tutor.services.config.settings import Settings, get_settings
from tutor.services.resource_package.schema import (
    ArtifactRef,
    CodeResource,
    Resource,
    ResourceType,
    build_resource,
)

CODE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "language": {"type": "string", "default": "python"},
        "code": {"type": "string"},
        "explanation": {"type": "string"},
        "expected_output": {"type": "string"},
        "difficulty": {"type": "integer", "minimum": 1, "maximum": 5},
    },
    "required": ["title", "code", "explanation"],
}


class CodeSandboxAgent(BaseAgent):
    """Generate a runnable code example."""

    module_name = "resource"
    agent_name = "code_sandbox"
    default_temperature = 0.3
    # **2026-07-07 fix:** raised from 2048 → 4096. A XOR/backprop
    # snippet with bilingual comments + multiple ``print`` lines
    # encoded as a JSON string (each ``\n`` becomes two chars in
    # the LLM output, then escapes again into the JSON wrapper)
    # routinely exceeds 2048 tokens, gets truncated mid-string,
    # and the salvage path returns a SyntaxError-filled block.
    default_max_tokens = 4096

    def __init__(self, *, settings: Settings | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._settings = settings

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        topic: str,
        source_content: str = "",
        profile: dict[str, Any] | None = None,
        run_locally: bool = True,
        timeout_seconds: int | None = None,
    ) -> Resource:
        prompt_data = self.get_prompt_data(context.language)
        system = self.get_system_prompt(prompt_data)
        user_msg = self.get_user_prompt(prompt_data).format(
            topic=topic,
            source_content=(source_content or "")[:4000],
            profile=json.dumps(profile or {}, ensure_ascii=False, indent=2),
        )
        messages = self.build_messages(system=system, user=user_msg)

        if stream is not None:
            async with stream.stage("code_generation", source=self.agent_name):
                await stream.thinking(
                    f"为「{topic}」生成代码示例...",
                    source=self.agent_name,
                    stage="code_generation",
                )
                resp, data, _attempts = await self.call_llm_with_retry(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="code_generation",
                    temperature=self.default_temperature,
                    response_format={"type": "json_object"},
                )
        else:
            resp, data, _attempts = await self.call_llm_with_retry(
                messages=messages,
                stream=None,
                source=self.agent_name,
                temperature=self.default_temperature,
                response_format={"type": "json_object"},
            )

        if not isinstance(data, dict):
            data = {}

        title = str(data.get("title") or f"{topic} — 代码示例")
        code = str(data.get("code") or "").strip()
        explanation = str(data.get("explanation") or "")
        language = str(data.get("language") or "python")
        difficulty = max(1, min(5, int(data.get("difficulty") or 3)))

        # **2026-06-22 fix (Task 8):** when the LLM returns the code
        # as a JSON string with literal newlines embedded (a common
        # failure mode — ``json.loads`` strictly rejects unescaped
        # control chars), ``parse_json_response`` falls back to ``{}``
        # and we end up with empty ``code``. Salvage by re-extracting
        # the first Python block from the raw response and
        # normalizing its escape sequences to real newlines.
        if not code:
            salvaged = _extract_first_python_block(resp.content)
            if salvaged:
                code = salvaged
                logger.info(
                    f"code_sandbox: salvaged code from non-JSON LLM output "
                    f"(topic={topic!r}, len={len(code)})"
                )
        if code:
            code = _normalize_code_newlines(code)

        # Strip code fences if present
        if code.startswith("```"):
            lines = code.splitlines()
            # Drop first ```python or ``` line and trailing ```
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            code = "\n".join(lines).strip()

        # Resolve the configured interpreter and timeout (2026-06-21 plan).
        settings = self._settings or get_settings()
        configured_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else int(getattr(settings, "code_run_timeout_seconds", 15))
        )
        interpreter = _resolve_execution_python(settings)

        execution_status = "not_run"
        stdout = ""
        stderr = ""
        error_code: str | None = None
        dependency_versions: dict[str, str] = {}
        artifacts: list[dict[str, str]] = []
        duration_seconds: float = 0.0
        # **2026-06-22 fix (Task 8):** if even the salvage path produced
        # no code, surface a typed failed resource rather than an empty
        # code block in the chat viewer.
        if not code:
            logger.warning(
                f"code_sandbox: LLM returned empty/non-Python code for topic={topic!r}"
            )
            payload = CodeResource(
                language=language,
                code="",
                explanation="",
                execution_status="failed",
                stdout="",
                stderr="LLM did not return usable Python code",
                error_code="CODE_EMPTY_LLM_OUTPUT",
                execution_python=interpreter,
            )
            markdown = (
                f"# {title}\n\n"
                f"**语言**：{language}\n\n"
                f"## 诊断\n\nLLM 未能为此主题生成有效的代码示例。\n\n"
                f"**建议**：重新提交请求或简化主题描述。\n"
            )
            failed_payload = payload.model_dump()
            failed_payload["failure"] = public_failure(
                "CODE_EMPTY_LLM_OUTPUT",
                "Code generation failed",
                retryable=True,
            )
            return build_resource(
                type=ResourceType.CODE,
                title=f"{title} — 代码生成失败",
                content=markdown,
                format_specific=failed_payload,
                difficulty=difficulty,
                estimated_minutes=0,
                prerequisites=[],
                generated_by=[self.agent_name],
                confidence_score=0.0,
                topic=topic,
                tags=["code", language, "failed", "codegen_empty"],
            )
        if (
            run_locally
            and language.lower() == "python"
            and code
            and len(code.splitlines()) <= 200
        ):
            (
                execution_status,
                stdout,
                stderr,
                error_code,
                dependency_versions,
                artifacts,
                duration_seconds,
            ) = _safe_run_python(
                code,
                interpreter=interpreter,
                timeout=configured_timeout,
                settings=settings,
            )

        # Subprocess diagnostics are untrusted strings. Preserve useful
        # educational errors while removing credential-shaped fragments.
        stdout = redact_text(stdout)
        stderr = redact_text(stderr)
        dependency_versions = redact_sensitive(dependency_versions)

        payload = CodeResource(
            language=language,
            code=code,
            explanation=explanation,
            execution_status=execution_status,  # type: ignore[arg-type]
            stdout=stdout[:2000],
            stderr=stderr[:2000],
        )

        # 2026-06-21 plan: surface the structured error code + the
        # resolved interpreter so the right-pane viewer can show
        # "环境缺失" vs "代码错误" with a retry hint.
        payload.error_code = error_code
        payload.execution_python = interpreter
        payload.duration_seconds = round(duration_seconds, 3)
        payload.dependency_versions = dependency_versions
        payload.artifacts = [ArtifactRef.model_validate(item) for item in artifacts]

        markdown = (
            f"# {title}\n\n"
            f"**语言**：{language}\n\n"
            f"## 说明\n\n{explanation}\n\n"
            f"## 代码\n\n```{language}\n{code}\n```\n"
        )
        if dependency_versions:
            vers = ", ".join(f"{k}={v}" for k, v in dependency_versions.items())
            interpreter_name = Path(interpreter).name or "python"
            markdown += f"\n**运行环境**：{interpreter_name}（{vers}）\n"
        if stdout:
            markdown += f"\n## 运行输出\n\n```\n{stdout}\n```\n"
        if stderr:
            markdown += f"\n## 错误\n\n```\n{stderr[:500]}\n```\n"
        if error_code:
            # Distinguish runtime dep missing from code bugs so the
            # user understands whether the issue is fixable.
            pretty = {
                "RUNTIME_DEPENDENCY_MISSING": "运行环境缺少依赖（pip install 后重试）",
                "CODE_EXECUTION_FAILED": "代码执行失败（请检查代码）",
                "CODE_RUN_TIMEOUT": "执行超时（请简化代码或拆分）",
                "CODE_RUNTIME_PREPARATION_FAILED": "代码运行环境准备失败（请检查数据目录权限）",
                "MATPLOTLIB_CAPTURE_FAILED": "Matplotlib 图片保存失败（请重试）",
            }.get(error_code, error_code)
            markdown += f"\n**错误类型**：{pretty}\n"
        if artifacts:
            markdown += "\n## 产物\n\n"
            for art in artifacts:
                markdown += f"- `{art['name']}` ({art['kind']})\n"

        confidence = 0.8 if execution_status == "success" else 0.6

        format_specific = payload.model_dump()
        if execution_status in {"failed", "timeout"}:
            format_specific["failure"] = public_failure(
                error_code or "CODE_EXECUTION_FAILED",
                "Code execution failed",
                retryable=execution_status == "timeout"
                or error_code == "RUNTIME_DEPENDENCY_MISSING",
            )

        return build_resource(
            type=ResourceType.CODE,
            title=title,
            content=markdown,
            format_specific=format_specific,
            difficulty=difficulty,
            estimated_minutes=5,
            prerequisites=[],
            generated_by=[self.agent_name],
            confidence_score=confidence,
            topic=topic,
            tags=["code", language],
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_execution_python(settings: Any) -> str:
    """Pick the Python interpreter to run the sandbox.

    Order:
      1. ``settings.execution_python`` if non-empty.
      2. ``sys.executable`` of the running backend (this is correct
         when the backend was launched via ``conda run -n tutor`` or
         inside a properly-constructed venv).
    """
    candidate = (getattr(settings, "execution_python", "") or "").strip()
    if candidate:
        return candidate
    return sys.executable


# Patterns that strongly suggest the code needs an extra runtime
# dependency. Keep this list tight — false positives mask real code
# bugs behind "RUNTIME_DEPENDENCY_MISSING".
_MISSING_MODULE_HINTS = (
    "ModuleNotFoundError",
    "ImportError",
)

_DEPENDENCY_PROBE_LOCK = threading.Lock()
_DEPENDENCY_PROBE_CACHE: dict[tuple[str, str], dict[str, str]] = {}


def _safe_run_python(
    code: str,
    *,
    interpreter: str,
    timeout: int,
    settings: Any,
) -> tuple[str, str, str, str | None, dict[str, str], list[dict[str, str]], float]:
    """Run ``code`` in a fresh subprocess with a per-run scratch dir.

    Returns ``(status, stdout, stderr, error_code, deps, artifacts, duration)``.

    - ``status`` is one of ``"success"``, ``"failed"``, ``"timeout"``,
      ``"not_run"``.
    - ``error_code`` is one of ``"RUNTIME_DEPENDENCY_MISSING"``,
      ``"CODE_EXECUTION_FAILED"``, ``"CODE_RUN_TIMEOUT"`` (or None
      on success).
    - ``deps`` is a small map of runtime versions for matplotlib,
      numpy, and the python interpreter itself, captured from a
      short probe import after the user's code (so an ImportError
      in user code does not poison the version snapshot).
    - ``artifacts`` is the list of image / svg files written by the
      user code that we want the UI to render.
    """
    started = time.monotonic()
    deps: dict[str, str] = {}
    try:
        code_runs_dir = settings.data_dir / getattr(
            settings, "code_run_subdir", "code_runs"
        )
        code_runs_dir.mkdir(parents=True, exist_ok=True)
        # Matplotlib's font/config cache is application data, not a run
        # artifact. Prepare it before the scratch directory so a cache
        # failure cannot leave an orphaned run directory.
        matplotlib_cache = (settings.data_dir / "cache" / "matplotlib").resolve()
        matplotlib_cache.mkdir(parents=True, exist_ok=True)
        # UUID names avoid same-millisecond collisions between concurrent
        # jobs in the same backend process.
        scratch = code_runs_dir / f"run_{uuid.uuid4().hex}"
        scratch.mkdir(parents=True, exist_ok=False)
        env = os.environ.copy()
        env["MPLBACKEND"] = "Agg"
        env["MPLCONFIGDIR"] = str(matplotlib_cache)
        env["PYTHONIOENCODING"] = "utf-8"
        deps = _cached_dependency_versions(
            interpreter,
            matplotlib_cache=matplotlib_cache,
            env=env,
        )
    except Exception:  # noqa: BLE001 - preparation failures are a typed result
        return (
            "failed",
            "",
            "[code runtime preparation failed]",
            "CODE_RUNTIME_PREPARATION_FAILED",
            {},
            [],
            time.monotonic() - started,
        )
    # Run with cwd=scratch so any relative file writes land in our
    # artifact directory.
    try:
        proc = subprocess.run(
            [interpreter, "-c", _wrap_user_code(code, scratch)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(scratch),
            env=env,
        )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - started
        return (
            "timeout",
            "",
            f"[timeout after {timeout}s]",
            "CODE_RUN_TIMEOUT",
            deps,
            [],
            duration,
        )
    except FileNotFoundError:
        # Interpreter binary not on PATH.
        duration = time.monotonic() - started
        return (
            "failed",
            "",
            "[configured interpreter unavailable]",
            "RUNTIME_DEPENDENCY_MISSING",
            deps,
            [],
            duration,
        )
    except Exception:  # noqa: BLE001
        duration = time.monotonic() - started
        return (
            "failed",
            "",
            "[code execution failed]",
            "CODE_EXECUTION_FAILED",
            deps,
            [],
            duration,
        )
    duration = time.monotonic() - started

    # Pick up any image artifacts the user code wrote to the
    # scratch dir. PNG / SVG / PDF are the formats that downstream
    # viewers (ResourceCard etc.) can render.
    # Matplotlib figures are drained INSIDE the subprocess (see
    # ``_wrap_user_code``) so they land on disk before the child exits.
    artifacts: list[dict[str, str]] = []
    try:
        for entry in sorted(scratch.iterdir(), key=_natural_path_key):
            if entry.is_file() and entry.suffix.lower() in {
                ".png",
                ".svg",
                ".pdf",
                ".jpg",
                ".jpeg",
            }:
                # Only include files that are not part of the
                # matplotlib cache directory.
                artifacts.append(
                    {
                        "name": entry.name,
                        "artifact_key": to_artifact_key(entry, settings.data_dir),
                        "kind": entry.suffix.lower().lstrip("."),
                    }
                )
    except OSError:
        # Best-effort — failing to enumerate the dir must not
        # affect the run result.
        pass

    stdout_text = _redact_scratch_path(proc.stdout or "", scratch)
    stderr_text = _redact_scratch_path(proc.stderr or "", scratch)
    if proc.returncode == 0:
        return ("success", stdout_text, stderr_text, None, deps, artifacts, duration)

    # Classify the failure: ModuleNotFoundError / ImportError on
    # the configured interpreter is a runtime-dep issue; everything
    # else is the LLM-generated code.
    if "[matplotlib capture failed]" in stderr_text:
        error_code = "MATPLOTLIB_CAPTURE_FAILED"
    elif any(hint in stderr_text for hint in _MISSING_MODULE_HINTS):
        error_code = "RUNTIME_DEPENDENCY_MISSING"
    else:
        error_code = "CODE_EXECUTION_FAILED"
    return ("failed", stdout_text, stderr_text, error_code, deps, artifacts, duration)


def _redact_scratch_path(text: str, scratch: Path) -> str:
    """Keep run-private absolute paths out of resource/API text fields."""
    redacted = text
    variants = {str(scratch), scratch.as_posix()}
    for value in sorted(variants, key=len, reverse=True):
        redacted = redacted.replace(value, "<sandbox>")
    return redacted


def _natural_path_key(path: Path) -> tuple[tuple[int, int | str], ...]:
    """Sort artifact names naturally (figure_2 before figure_10)."""
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", path.name)
    )


def _cached_dependency_versions(
    interpreter: str,
    *,
    matplotlib_cache: Path,
    env: dict[str, str],
) -> dict[str, str]:
    """Probe once per interpreter/cache pair and serialize first warm-up."""
    resolved_interpreter = shutil.which(interpreter) or str(
        Path(interpreter).expanduser().resolve()
    )
    key = (resolved_interpreter, str(matplotlib_cache))
    with _DEPENDENCY_PROBE_LOCK:
        cached = _DEPENDENCY_PROBE_CACHE.get(key)
        if cached is not None:
            return dict(cached)
        versions = _probe_dependency_versions(interpreter, env=env)
        if "probe_error" not in versions:
            _DEPENDENCY_PROBE_CACHE[key] = dict(versions)
        return versions


def _probe_dependency_versions(
    interpreter: str,
    *,
    env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Print the interpreter version + (best-effort) matplotlib and
    numpy versions from a short probe script.

    **2026-06-22 fix (Task 4):** the pre-fix probe silently returned
    ``{}`` on any syntax/probe failure, which was indistinguishable
    from "the probe ran but didn't find the packages." We now
    return ``{"python": sys_version}`` as the minimal payload and
    add ``probe_error`` / ``probe_stderr`` keys when the probe fails,
    so the operator can see that the interpreter itself is broken
    vs. simply lacking optional packages.
    """
    probe = (
        "import sys, json\n"
        "out = {'python': sys.version.split()[0]}\n"
        "try:\n"
        "    import matplotlib\n"
        "    out['matplotlib'] = matplotlib.__version__\n"
        "except ImportError:\n"
        "    out['matplotlib'] = 'not_installed'\n"
        "except Exception:\n"
        "    out['matplotlib'] = 'error'\n"
        "try:\n"
        "    import numpy\n"
        "    out['numpy'] = numpy.__version__\n"
        "except ImportError:\n"
        "    out['numpy'] = 'not_installed'\n"
        "except Exception:\n"
        "    out['numpy'] = 'error'\n"
        "print(json.dumps(out))\n"
    )
    try:
        proc = subprocess.run(
            [interpreter, "-c", probe],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            env=env,
        )
    except FileNotFoundError:
        return {
            "python": "unknown",
            "probe_error": "DEPENDENCY_INTERPRETER_UNAVAILABLE",
        }
    except Exception:  # noqa: BLE001
        return {"python": "unknown", "probe_error": "DEPENDENCY_PROBE_FAILED"}
    if proc.returncode != 0:
        return {
            "python": "unknown",
            "probe_error": "DEPENDENCY_PROBE_FAILED",
        }
    last = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    try:
        import json as _json

        parsed = {k: str(v) for k, v in _json.loads(last).items()}
        if any(value == "error" for value in parsed.values()):
            return {
                "python": parsed.get("python", "unknown"),
                "probe_error": "DEPENDENCY_PROBE_FAILED",
            }
        return parsed
    except Exception:
        return {
            "python": "unknown",
            "probe_error": "DEPENDENCY_PROBE_FAILED",
        }


__all__ = ["CodeSandboxAgent", "CODE_OUTPUT_SCHEMA"]


def _wrap_user_code(code: str, scratch: Path) -> str:
    """Wrap ``code`` with a post-run matplotlib drain.

    The sandbox forces ``MPLBACKEND=Agg`` and replaces ``plt.show()``
    with a deterministic capture hook. This avoids the non-interactive
    canvas warning and preserves figures even when user code closes them
    after showing.

    Wrapping the user's snippet in a ``try / finally`` ensures the
    drain runs even if the snippet raises. The drain is done INSIDE
    the subprocess because by the time the parent picks up the
    artifacts, the child has exited and its figures are gone.

    **2026-07-08 fix (585f367d trace):** the wrapper now also
    pre-configures matplotlib's ``font.sans-serif`` list with a
    CJK-capable font BEFORE the user code runs. Pre-fix, matplotlib
    fell back to ``DejaVu Sans`` (Latin-only) and emitted one
    ``Glyph NNN missing from font(s) DejaVu Sans`` warning per CJK
    character; the resulting PNG contained empty squares instead of
    the labels (e.g. ``plt.title('训练损失')``). The list is
    ordered best-to-worst so hosts with Noto installed pick it
    first; the remaining names are no-ops on hosts that lack them
    but matplotlib skips them cleanly.
    """
    scratch_literal = repr(str(scratch))
    return (
        # 1. Configure matplotlib for CJK BEFORE importing pyplot so
        #    the rcParams take effect on the first figure.
        "import sys as _sys\n"
        "try:\n"
        "    import matplotlib as _mpl\n"
        "    _mpl.rcParams['font.sans-serif'] = [\n"
        "        'Noto Sans CJK SC', 'Noto Sans CJK JP',\n"
        "        'Source Han Sans CN', 'Source Han Sans SC',\n"
        "        'WenQuanYi Zen Hei', 'WenQuanYi Micro Hei',\n"
        "        'SimHei', 'Microsoft YaHei',\n"
        "        'PingFang SC', 'Hiragino Sans GB',\n"
        "        'Arial Unicode MS', 'DejaVu Sans',\n"
        "    ]\n"
        "    _mpl.rcParams['axes.unicode_minus'] = False\n"
        "except Exception:\n"
        "    pass\n"
        # 2. Force Agg and install a capture-based pyplot.show before
        #    user code imports pyplot. A WeakSet tracks figure objects,
        #    not figure numbers, because matplotlib can reuse number 1
        #    after a close. The counter remains monotonic for the run.
        "try:\n"
        "    import matplotlib as _mpl\n"
        "    _mpl.use('Agg', force=False)\n"
        "except Exception:\n"
        "    pass\n"
        "import weakref as _weakref\n"
        "import matplotlib.pyplot as _tutor_plt\n"
        "_tutor_captured_figures = _weakref.WeakSet()\n"
        "_tutor_figure_index = 0\n"
        "def _tutor_capture_figures():\n"
        "    global _tutor_figure_index\n"
        "    for _number in list(_tutor_plt.get_fignums()):\n"
        "        _figure = _tutor_plt.figure(_number)\n"
        "        if _figure in _tutor_captured_figures:\n"
        "            continue\n"
        "        _next_index = _tutor_figure_index + 1\n"
        "        _figure.savefig(\n"
        f"            {scratch_literal} + '/figure_' + str(_next_index) + '.png',\n"
        "            format='png', bbox_inches='tight', dpi=160,\n"
        "        )\n"
        "        _tutor_figure_index = _next_index\n"
        "        _tutor_captured_figures.add(_figure)\n"
        "def _tutor_show(*_args, **_kwargs):\n"
        "    try:\n"
        "        _tutor_capture_figures()\n"
        "    except Exception:\n"
        "        _sys.stderr.write('[matplotlib capture failed]\\n')\n"
        "        raise RuntimeError('MATPLOTLIB_CAPTURE_FAILED') from None\n"
        "_tutor_plt.show = _tutor_show\n"
        "_user_globals = {}\n"
        "_tutor_user_failed = False\n"
        "try:\n"
        "    exec(compile(" + repr(code) + ", '<sandbox>', 'exec'), _user_globals)\n"
        "except BaseException as _user_exc:\n"
        "    _tutor_user_failed = True\n"
        "    _sys.stderr.write(f'[user code raised: {type(_user_exc).__name__}: {_user_exc}]\\n')\n"
        "    raise\n"
        "finally:\n"
        "    try:\n"
        "        _tutor_capture_figures()\n"
        "    except Exception:\n"
        "        if not _tutor_user_failed:\n"
        "            _sys.stderr.write('[matplotlib capture failed]\\n')\n"
        "            raise RuntimeError('MATPLOTLIB_CAPTURE_FAILED') from None\n"
    )
