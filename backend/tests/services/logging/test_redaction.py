"""Logging-boundary redaction must be stricter than public artifacts."""

from __future__ import annotations

import copy
import json

from tutor.services.logging import redact_sensitive


class _ExplosiveDiagnostic:
    def __repr__(self) -> str:
        raise AssertionError("logging redaction must not call repr")

    def __str__(self) -> str:
        raise AssertionError("logging redaction must not call str")


def test_redacts_nested_secrets_private_fields_and_full_source() -> None:
    source = "def answer():\n    return '秘密'\n"
    value = {
        "api_key": "sk-private-value",
        "APIKey": "short-secret",
        "nested": [
            {"authorization": "Bearer credential"},
            ({"access-token": "access-value"}, {"Password": "hunter2"}),
        ],
        "hidden_tests": [{"call": "answer()", "expected": 42}],
        "private_reasoning": "private scratchpad",
        "chainOfThought": "step one",
        "system_prompt": "do not reveal",
        "messages": [{"role": "system", "content": "internal instruction"}],
        "source_code": source,
        "submittedCode": source,
        "submission": source,
    }

    public = redact_sensitive(value)
    encoded = json.dumps(public, ensure_ascii=False)

    for forbidden in (
        "sk-private-value",
        "short-secret",
        "credential",
        "access-value",
        "hunter2",
        "answer()",
        "private scratchpad",
        "step one",
        "do not reveal",
        "internal instruction",
        "return '秘密'",
    ):
        assert forbidden not in encoded
    assert public["source_code"] == f"[REDACTED:{len(source)} chars]"
    assert public["submittedCode"] == f"[REDACTED:{len(source)} chars]"
    assert public["submission"] == f"[REDACTED:{len(source)} chars]"


def test_preserves_safe_operational_fields_without_mutating_input() -> None:
    value = {
        "error_code": "CODE_TIMEOUT",
        "status": "failed",
        "duration_ms": 125,
        "passed_tests": 3,
        "artifact_key": "jobs/job-1/render.log",
        "summary": "渲染超时，请重试",
        "resource_url": "https://example.invalid/resource/1",
        "answer": "梯度下降通过迭代更新参数。",
        "items": ({"count": 2}, ["安全内容"]),
    }
    original = copy.deepcopy(value)

    public = redact_sensitive(value)

    assert value == original
    assert public == {
        **{key: value[key] for key in value if key != "items"},
        "items": [{"count": 2}, ["安全内容"]],
    }


def test_bounds_deep_recursive_and_unsupported_values_without_rendering_them() -> None:
    recursive: list[object] = []
    recursive.append(recursive)
    deep: dict[str, object] = {}
    cursor = deep
    for index in range(20):
        child: dict[str, object] = {"level": index}
        cursor["child"] = child
        cursor = child

    public = redact_sensitive(
        {
            "recursive": recursive,
            "deep": deep,
            "long_summary": "界" * 10_000,
            "unknown": _ExplosiveDiagnostic(),
            "many": list(range(1_000)),
        }
    )
    encoded = json.dumps(public, ensure_ascii=False)

    assert "[RECURSIVE]" in encoded
    assert "[MAX_DEPTH]" in encoded
    assert "[TRUNCATED]" in encoded
    assert public["unknown"] == "[UNSUPPORTED:_ExplosiveDiagnostic]"
    assert len(encoded) < 20_000


def test_redacts_credential_assignments_inside_safe_summary_text() -> None:
    public = redact_sensitive(
        {
            "summary": (
                "request failed: password=hunter2, "
                "authorization: Bearer abc and api-key='tiny'"
            )
        }
    )

    encoded = json.dumps(public)
    assert "hunter2" not in encoded
    assert "Bearer abc" not in encoded
    assert "tiny" not in encoded


def test_decodes_invalid_diagnostic_bytes_then_redacts_and_bounds_them() -> None:
    public = redact_sensitive(
        {
            "diagnostic_bytes": "中文".encode() + b"\xff",
            "secret_bytes": b"password=hunter2",
            "long_bytes": b"x" * 10_000,
        }
    )

    diagnostic = public["diagnostic_bytes"]
    assert isinstance(diagnostic, str)
    assert "�" in diagnostic
    assert "hunter2" not in public["secret_bytes"]
    assert "[TRUNCATED]" in public["long_bytes"]


def test_redacts_vendor_assignments_and_source_prompt_key_variants() -> None:
    original_code = "print('private submission')"
    public = redact_sensitive(
        {
            "stderr": (
                "MINIMAX_API_KEY=DEMO_SECRET_VALUE "
                "token=DEMO_TOKEN_VALUE"
            ),
            "token": "DEMO_DIRECT_TOKEN",
            "original_code": original_code,
            "current_source": "private current source",
            "repair_prompt": "private repair instruction",
            "starter_code": "private starter code",
            "python_code": "private python code",
            "prompt_template": "private prompt template",
            "prompt_content": "private prompt content",
            "system_messages": ["private system message"],
            "prompt_tokens": 23,
            "error_code": "MCP_SERVER_FAILED",
        }
    )
    encoded = json.dumps(public)

    for forbidden in (
        "DEMO_SECRET_VALUE",
        "DEMO_TOKEN_VALUE",
        "DEMO_DIRECT_TOKEN",
        "private submission",
        "private current source",
        "private repair instruction",
        "private starter code",
        "private python code",
        "private prompt template",
        "private prompt content",
        "private system message",
    ):
        assert forbidden not in encoded
    assert public["token"] == "[REDACTED]"
    assert public["original_code"] == f"[REDACTED:{len(original_code)} chars]"
    assert public["prompt_tokens"] == 23
    assert public["error_code"] == "MCP_SERVER_FAILED"
