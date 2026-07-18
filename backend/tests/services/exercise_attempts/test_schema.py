from __future__ import annotations

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
    assert choice["answer"] is True
    serialized = str(public)
    assert "private reference" not in serialized
    assert "expected_json" not in serialized
    assert "add(1, 2)" not in serialized
