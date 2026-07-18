"""Regression coverage for resource-shaped public job data."""

from __future__ import annotations

import json

from tutor.core.redaction import REDACTED
from tutor.services.jobs.runner import JobRunner
from tutor.services.resource_package.public_projection import (
    project_public_event,
    project_public_payload,
)


def exercise_resource_event(*, options: list[dict[str, str]]) -> dict[str, object]:
    """Build the public event shape emitted for a validated exercise resource."""
    return {
        "type": "resource",
        "content": "",
        "metadata": {
            "resource": {
                "resource_id": "exercise-1",
                "type": "exercise",
                "title": "梯度练习",
                "content": "",
                "format_specific": {
                    "questions": [
                        {
                            "id": "q-1",
                            "type": "single_choice",
                            "question": "什么是梯度？",
                            "options": options,
                        }
                    ],
                    "total_questions": 1,
                },
            }
        },
    }


def exercise_package_payload(*, options: list[dict[str, str]]) -> dict[str, object]:
    event = exercise_resource_event(options=options)
    return {
        "package": {
            "package_id": "package-1",
            "topic": "梯度",
            "resources": [event["metadata"]["resource"]],
        }
    }


def test_public_resource_event_preserves_nested_exercise_options() -> None:
    event = exercise_resource_event(
        options=[{"label": "A", "text": "梯度"}, {"label": "B", "text": "损失"}]
    )
    projected = project_public_event(event)
    options = projected["metadata"]["resource"]["format_specific"]["questions"][0]["options"]
    assert options == [{"label": "A", "text": "梯度"}, {"label": "B", "text": "损失"}]
    assert "[TRUNCATED]" not in json.dumps(projected, ensure_ascii=False)


def test_public_resource_projection_still_redacts_credentials_and_sensitive_metadata() -> None:
    event = exercise_resource_event(options=[{"label": "A", "text": "梯度"}])
    metadata = event["metadata"]
    assert isinstance(metadata, dict)
    metadata["api_key"] = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    metadata["authorization"] = "Bearer SECRET_TOKEN_public-event"
    metadata["credential_text"] = "token=SECRET_TOKEN_embedded"
    metadata["private_reasoning"] = "internal only"

    projected = project_public_event(event)

    projected_metadata = projected["metadata"]
    assert projected_metadata["api_key"] == REDACTED
    assert projected_metadata["authorization"] == REDACTED
    assert REDACTED in projected_metadata["credential_text"]
    assert projected_metadata["private_reasoning"] == REDACTED


def test_public_terminal_package_payload_preserves_nested_exercise_options() -> None:
    payload = exercise_package_payload(
        options=[{"label": "A", "text": "梯度"}, {"label": "B", "text": "损失"}]
    )

    projected = project_public_payload(payload)

    options = projected["package"]["resources"][0]["format_specific"]["questions"][0]["options"]
    assert options == [{"label": "A", "text": "梯度"}, {"label": "B", "text": "损失"}]
    assert "[TRUNCATED]" not in json.dumps(projected, ensure_ascii=False)


def test_public_resource_event_applies_public_resource_rules_with_schema_defaults() -> None:
    event = exercise_resource_event(options=[])
    resource = event["metadata"]["resource"]
    assert isinstance(resource, dict)
    resource.pop("resource_id")
    format_specific = resource["format_specific"]
    assert isinstance(format_specific, dict)
    format_specific["questions"] = [
        {
            "id": "code-1",
            "type": "code",
            "question": "实现梯度下降",
            "answer": "private reference solution",
            "code_spec": {"tests": [{"name": "hidden", "call": "f()", "expected_json": 1}]},
        }
    ]

    projected = project_public_event(event)

    question = projected["metadata"]["resource"]["format_specific"]["questions"][0]
    assert "answer" not in question
    assert "private reference solution" not in json.dumps(projected, ensure_ascii=False)


def test_public_resource_projection_redacts_host_paths() -> None:
    event = exercise_resource_event(options=[{"label": "A", "text": "梯度"}])
    metadata = event["metadata"]
    assert isinstance(metadata, dict)
    metadata["diagnostic_path"] = "C:\\Users\\agent\\private.log"

    projected = project_public_event(event)

    assert projected["metadata"]["diagnostic_path"] == REDACTED


def test_runner_routes_resource_events_through_schema_aware_projection() -> None:
    class ResourceEvent:
        def to_dict(self) -> dict[str, object]:
            return exercise_resource_event(
                options=[{"label": "A", "text": "梯度"}, {"label": "B", "text": "损失"}]
            )

    projected = JobRunner._normalize_capability_event(ResourceEvent(), "resource")

    options = projected["metadata"]["resource"]["format_specific"]["questions"][0]["options"]
    assert options == [{"label": "A", "text": "梯度"}, {"label": "B", "text": "损失"}]


def test_public_terminal_payload_bounds_cyclic_unknown_data() -> None:
    cyclic: dict[str, object] = {}
    cyclic["cycle"] = cyclic

    projected = project_public_payload({"unknown": cyclic})

    assert projected["unknown"]["cycle"] == {"[TRUNCATED]": "[TRUNCATED]"}
