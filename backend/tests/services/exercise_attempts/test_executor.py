from __future__ import annotations

import sys

from tutor.agents.resource.code_sandbox import run_code_submission
from tutor.services.resource_package.schema import CodeSpec


def _spec(*tests, timeout: int = 3) -> CodeSpec:
    return CodeSpec.model_validate(
        {
            "starter_code": "",
            "tests": list(tests),
            "time_limit_seconds": timeout,
        }
    )


def test_submission_runs_all_server_tests_and_caps_output() -> None:
    result = run_code_submission(
        "print('x' * 50000)\ndef add(a, b): return a + b",
        code_spec=_spec(
            {"name": "first", "call": "add(1, 2)", "expected_json": 3},
            {"name": "second", "call": "add(-2, 5)", "expected_json": 3},
        ),
        interpreter=sys.executable,
    )
    assert result.status == "passed"
    assert result.passed_tests == 2
    assert len(result.stdout.encode("utf-8")) <= 16 * 1024
    assert all(item.passed for item in result.test_results)


def test_submission_continues_after_a_failed_test_without_leaking_expected() -> None:
    result = run_code_submission(
        "def add(a, b): return a - b",
        code_spec=_spec(
            {"name": "wrong", "call": "add(1, 2)", "expected_json": 3},
            {"name": "still runs", "call": "add(5, 2)", "expected_json": 3},
        ),
        interpreter=sys.executable,
    )
    assert result.status == "failed"
    assert [item.passed for item in result.test_results] == [False, True]
    assert result.test_results[0].actual_json == -1
    assert "expected" not in result.model_dump_json().lower()


def test_submission_classifies_syntax_timeout_and_policy_without_paths(tmp_path) -> None:
    syntax = run_code_submission(
        "def broken(:\n    pass",
        code_spec=_spec({"name": "x", "call": "x()", "expected_json": 1}),
        interpreter=sys.executable,
    )
    assert syntax.status == "syntax_error"
    assert syntax.test_results == []

    policy = run_code_submission(
        "import os\ndef x(): return os.getcwd()",
        code_spec=_spec({"name": "x", "call": "x()", "expected_json": "secret"}),
        interpreter=sys.executable,
    )
    assert policy.status == "policy_rejected"
    assert policy.error_code == "CODE_POLICY_VIOLATION"

    timed = run_code_submission(
        "def loop():\n    while True: pass",
        code_spec=_spec(
            {"name": "loop", "call": "loop()", "expected_json": None}, timeout=1
        ),
        interpreter=sys.executable,
    )
    assert timed.status == "timeout"
    serialized = timed.model_dump_json()
    assert str(tmp_path) not in serialized
    assert "Traceback" not in serialized


def test_submission_does_not_fallback_when_interpreter_is_unavailable() -> None:
    result = run_code_submission(
        "def x(): return 1",
        code_spec=_spec({"name": "x", "call": "x()", "expected_json": 1}),
        interpreter="Z:/missing/tutor-python.exe",
    )
    assert result.status == "error"
    assert result.error_code == "CODE_INTERPRETER_UNAVAILABLE"
    assert result.test_results == []


def test_submission_policy_covers_server_test_calls() -> None:
    result = run_code_submission(
        "def x(): return 1",
        code_spec=_spec(
            {
                "name": "dangerous persisted test",
                "call": "__import__('os').getcwd()",
                "expected_json": "hidden",
            }
        ),
        interpreter=sys.executable,
    )
    assert result.status == "policy_rejected"
    assert result.error_code == "CODE_POLICY_VIOLATION"


def test_large_valid_source_is_not_passed_on_the_command_line() -> None:
    padding = "# padding\n" * 9000
    result = run_code_submission(
        padding + "\ndef answer(): return 42",
        code_spec=_spec(
            {"name": "answer", "call": "answer()", "expected_json": 42}
        ),
        interpreter=sys.executable,
    )
    assert result.status == "passed"


def test_submission_caps_large_test_results_before_the_sentinel() -> None:
    result = run_code_submission(
        "def huge(): return 'x' * 100000",
        code_spec=_spec(
            {"name": "bounded", "call": "huge()", "expected_json": "different"}
        ),
        interpreter=sys.executable,
    )
    assert result.status == "failed"
    assert result.test_results[0].error_code == "CODE_RESULT_TOO_LARGE"
    assert len(result.model_dump_json().encode("utf-8")) < 20 * 1024
