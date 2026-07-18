from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from tutor.agents.resource.exercise_generator import EXERCISE_OUTPUT_SCHEMA
from tutor.services.resource_package.schema import (
    CodeSpec,
    ExerciseQuestion,
    ResourcePackage,
    ResourceType,
    build_resource,
    public_package_dump,
)


def test_code_spec_is_strict_and_bounded() -> None:
    spec = CodeSpec.model_validate(
        {
            "language": "python",
            "starter_code": "def add(a, b):\n    pass",
            "tests": [{"name": "adds", "call": "add(1, 2)", "expected_json": 3}],
            "time_limit_seconds": 5,
        }
    )
    assert spec.tests[0].expected_json == 3

    with pytest.raises(ValidationError):
        CodeSpec.model_validate(
            {"starter_code": "pass", "tests": [], "unexpected": True}
        )
    with pytest.raises(ValidationError):
        CodeSpec.model_validate(
            {
                "language": "javascript",
                "starter_code": "",
                "tests": [{"name": "x", "call": "x()", "expected_json": None}],
            }
        )
    with pytest.raises(ValidationError):
        CodeSpec.model_validate(
            {
                "starter_code": "pass",
                "tests": [
                    {"name": str(i), "call": "f()", "expected_json": i}
                    for i in range(51)
                ],
            }
        )


@pytest.mark.parametrize(
    "invalid_expected",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        {"not", "json"},
        {1: "non-string key"},
        {"nested": (1, 2)},
        {"custom": object()},
        "\ud800",
        {"\ud800": "unpaired surrogate key"},
    ],
)
def test_code_spec_rejects_non_standard_json_expected_values(
    invalid_expected,
) -> None:
    with pytest.raises(ValidationError):
        CodeSpec.model_validate(
            {
                "starter_code": "def solve(): pass",
                "tests": [
                    {
                        "name": "strict json",
                        "call": "solve()",
                        "expected_json": invalid_expected,
                    }
                ],
            }
        )


def test_code_spec_expected_json_has_deterministic_standard_roundtrip() -> None:
    expected = {
        "none": None,
        "boolean": True,
        "number": 1.25,
        "text": "中文",
        "array": [1, "two", {"three": 3}],
    }
    spec = CodeSpec.model_validate(
        {
            "starter_code": "def solve(): pass",
            "tests": [
                {"name": "json", "call": "solve()", "expected_json": expected}
            ],
        }
    )
    encoded = json.dumps(
        spec.tests[0].expected_json,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert json.dumps(
        json.loads(encoded),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ) == encoded


def test_legacy_code_question_without_spec_remains_viewable() -> None:
    question = ExerciseQuestion(
        id="legacy",
        type="code",
        question="Legacy question",
        answer="def solve(): pass",
    )
    assert question.type == "code"
    assert question.code_spec is None


def test_generator_schema_requires_code_spec_shape() -> None:
    item = EXERCISE_OUTPUT_SCHEMA["properties"]["questions"]["items"]
    assert "code_spec" in item["properties"]
    code_spec = item["properties"]["code_spec"]
    assert code_spec["properties"]["language"]["const"] == "python"
    assert set(code_spec["required"]) == {"language", "starter_code", "tests"}
    expected_schema = code_spec["properties"]["tests"]["items"]["properties"][
        "expected_json"
    ]
    assert set(expected_schema["type"]) == {
        "null",
        "boolean",
        "number",
        "string",
        "array",
        "object",
    }
    prompt = Path(
        "backend/tutor/prompts/resource/zh/exercise_generator.yaml"
    ).read_text(encoding="utf-8")
    assert "标准 JSON" in prompt
    assert "NaN" in prompt


def test_public_package_projection_never_exposes_code_answers_or_tests() -> None:
    package = ResourcePackage(
        package_id="pkg",
        topic="python",
        resources=[
            build_resource(
                type=ResourceType.EXERCISE,
                title="Exercises",
                content="**答案**：private reference implementation",
                format_specific={
                    "questions": [
                        {
                            "id": "code",
                            "type": "code",
                            "question": "Implement add",
                            "answer": "private reference implementation",
                            "code_spec": {
                                "language": "python",
                                "starter_code": "def add(a, b): pass",
                                "tests": [
                                    {
                                        "name": "hidden",
                                        "call": "add(1, 2)",
                                        "expected_json": 3,
                                    }
                                ],
                                "time_limit_seconds": 5,
                            },
                        },
                        {
                            "id": "choice",
                            "type": "true_false",
                            "question": "Visible question",
                            "answer": True,
                        },
                    ]
                },
            )
        ],
    )
    public = public_package_dump(package)
    resource = public["resources"][0]
    code, choice = resource["format_specific"]["questions"]
    assert resource["content"] == ""
    assert "answer" not in code
    assert code["code_spec"] == {
        "language": "python",
        "starter_code": "def add(a, b): pass",
        "time_limit_seconds": 5,
        "test_count": 1,
    }
    assert "answer" not in choice
    serialized = str(public)
    assert "private reference" not in serialized
    assert "expected_json" not in serialized
    assert "add(1, 2)" not in serialized
