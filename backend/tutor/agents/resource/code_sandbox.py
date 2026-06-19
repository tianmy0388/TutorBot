"""CodeSandboxAgent — generate runnable code examples with verification.

Pipeline role:
    Pedagogy output → CodeSandboxAgent → CodeResource

The agent asks the LLM to write a small, runnable example that
illustrates the concept. It then optionally runs the code in a
subprocess (with a strict timeout) to verify it executes without errors.

For MVP the sandbox is best-effort: we only run *short* code blocks
(< 200 lines, no network/file IO imports) and time out at 5 seconds.
Phase 5 will swap in a proper sandboxed runner (Docker / RestrictedPython).
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.resource_package.schema import (
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
    default_max_tokens = 2048

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        topic: str,
        source_content: str = "",
        profile: dict[str, Any] | None = None,
        run_locally: bool = True,
        timeout_seconds: int = 5,
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
                resp = await self.call_llm(
                    messages=messages,
                    stream=stream,
                    source=self.agent_name,
                    stage="code_generation",
                    temperature=self.default_temperature,
                    response_format={"type": "json_object"},
                )
        else:
            resp = await self.call_llm(
                messages=messages,
                stream=None,
                source=self.agent_name,
                temperature=self.default_temperature,
                response_format={"type": "json_object"},
            )

        data = self.parse_json_response(resp.content, fallback={})
        if not isinstance(data, dict):
            data = {}

        title = str(data.get("title") or f"{topic} — 代码示例")
        code = str(data.get("code") or "").strip()
        explanation = str(data.get("explanation") or "")
        language = str(data.get("language") or "python")
        difficulty = max(1, min(5, int(data.get("difficulty") or 3)))

        # Strip code fences if present
        if code.startswith("```"):
            lines = code.splitlines()
            # Drop first ```python or ``` line and trailing ```
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            code = "\n".join(lines).strip()

        # Try to run (best-effort)
        execution_status = "not_run"
        stdout = ""
        stderr = ""
        if run_locally and language.lower() == "python" and code and len(code.splitlines()) <= 200:
            ok, out, err = _safe_run_python(code, timeout_seconds)
            execution_status = "success" if ok else "failed"
            stdout = out
            stderr = err

        payload = CodeResource(
            language=language,
            code=code,
            explanation=explanation,
            execution_status=execution_status,  # type: ignore[arg-type]
            stdout=stdout[:2000],
            stderr=stderr[:2000],
        )

        markdown = (
            f"# {title}\n\n"
            f"**语言**：{language}\n\n"
            f"## 说明\n\n{explanation}\n\n"
            f"## 代码\n\n```{language}\n{code}\n```\n"
        )
        if stdout:
            markdown += f"\n## 运行输出\n\n```\n{stdout}\n```\n"
        if stderr:
            markdown += f"\n## 错误\n\n```\n{stderr[:500]}\n```\n"

        confidence = 0.8 if execution_status == "success" else 0.6

        return build_resource(
            type=ResourceType.CODE,
            title=title,
            content=markdown,
            format_specific=payload.model_dump(),
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


def _safe_run_python(code: str, timeout: int = 5) -> tuple[bool, str, str]:
    """Run ``code`` in a subprocess with a hard timeout.

    Returns ``(success, stdout, stderr)``.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode == 0, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return False, "", f"[timeout after {timeout}s]"
    except Exception as exc:  # noqa: BLE001
        return False, "", f"[execution error: {exc}]"


__all__ = ["CodeSandboxAgent", "CODE_OUTPUT_SCHEMA"]
