from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from time import perf_counter
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict
from tutor.runtime.workflow_graph import (
    NodeOutcome,
    WorkflowGraph,
    WorkflowNode,
)
from tutor.services.resource_package.schema import Resource, ResourceType


class _EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _IntegerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: int


class _IntegerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: _IntegerOutput


class _StrictDependencyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    failed: _IntegerOutput
    healthy: _IntegerOutput


class _PartialHealthyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    healthy: _IntegerOutput


class _ResourceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource: Resource


class _ResourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: _ResourceOutput


class _SeenOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    marker: str


async def _value(value: Any) -> Any:
    return value


def test_graph_rejects_missing_dependencies_at_construction() -> None:
    with pytest.raises(ValueError, match="missing dependency.*missing"):
        WorkflowGraph([WorkflowNode("consumer", ("missing",), 1.0, _value)])


def test_graph_rejects_duplicate_names_at_construction() -> None:
    with pytest.raises(ValueError, match="duplicate node name.*same"):
        WorkflowGraph(
            [
                WorkflowNode("same", (), 1.0, _value),
                WorkflowNode("same", (), 1.0, _value),
            ]
        )


def test_graph_rejects_cycles_at_construction() -> None:
    with pytest.raises(ValueError, match="cycle"):
        WorkflowGraph(
            [
                WorkflowNode("left", ("right",), 1.0, _value),
                WorkflowNode("right", ("left",), 1.0, _value),
            ]
        )


@pytest.mark.asyncio
async def test_graph_obeys_dependency_order() -> None:
    calls: list[str] = []

    async def source(_inputs: Mapping[str, Any]) -> str:
        calls.append("source")
        return "ready"

    async def consumer(inputs: Mapping[str, Any]) -> str:
        assert inputs["source"] == "ready"
        calls.append("consumer")
        return "done"

    execution = await WorkflowGraph(
        [
            WorkflowNode("source", (), 1.0, source),
            WorkflowNode("consumer", ("source",), 1.0, consumer),
        ]
    ).execute({})

    assert calls == ["source", "consumer"]
    assert execution.outcomes["consumer"].output == "done"


@pytest.mark.asyncio
async def test_graph_runs_ready_siblings_concurrently() -> None:
    async def source(_inputs: Mapping[str, Any]) -> str:
        return "ready"

    async def sibling(inputs: Mapping[str, Any]) -> str:
        assert inputs["source"] == "ready"
        await asyncio.sleep(0.05)
        return "done"

    graph = WorkflowGraph(
        [
            WorkflowNode("source", (), 1.0, source),
            WorkflowNode("code", ("source",), 1.0, sibling),
            WorkflowNode("exercise", ("source",), 1.0, sibling),
        ]
    )
    started = perf_counter()
    execution = await graph.execute({})

    assert execution.outcomes["code"].status == "succeeded"
    assert execution.outcomes["exercise"].status == "succeeded"
    assert perf_counter() - started < 0.09


@pytest.mark.asyncio
async def test_timeout_uses_explicit_degradation() -> None:
    async def slow(_inputs: Mapping[str, Any]) -> str:
        await asyncio.sleep(1)
        return "late"

    async def degrade(_inputs: Mapping[str, Any], error_code: str) -> str:
        assert error_code == "WORKFLOW_NODE_TIMEOUT"
        return "fallback"

    execution = await WorkflowGraph([WorkflowNode("slow", (), 0.01, slow, degrade)]).execute({})

    assert execution.outcomes["slow"] == NodeOutcome(
        status="degraded",
        output="fallback",
        error_code="WORKFLOW_NODE_TIMEOUT",
    )


@pytest.mark.asyncio
async def test_timeout_without_degradation_fails_with_stable_code() -> None:
    async def slow(_inputs: Mapping[str, Any]) -> str:
        await asyncio.sleep(1)
        return "late"

    execution = await WorkflowGraph([WorkflowNode("slow", (), 0.01, slow)]).execute({})

    assert execution.outcomes["slow"] == NodeOutcome(
        status="failed",
        output=None,
        error_code="WORKFLOW_NODE_TIMEOUT",
    )


@pytest.mark.asyncio
async def test_failed_upstream_skips_consumer_without_degradation() -> None:
    async def fail(_inputs: Mapping[str, Any]) -> str:
        raise RuntimeError("provider secret must not leak")

    execution = await WorkflowGraph(
        [
            WorkflowNode("source", (), 1.0, fail),
            WorkflowNode("consumer", ("source",), 1.0, _value),
        ]
    ).execute({})

    assert execution.outcomes["source"].error_code == "WORKFLOW_NODE_FAILED"
    assert execution.outcomes["consumer"] == NodeOutcome(
        status="skipped",
        output=None,
        error_code="WORKFLOW_DEPENDENCY_FAILED",
    )
    assert "secret" not in repr(execution.outcomes)


@pytest.mark.asyncio
async def test_failed_upstream_can_explicitly_degrade_consumer() -> None:
    async def fail(_inputs: Mapping[str, Any]) -> str:
        raise RuntimeError("boom")

    async def degrade(inputs: Mapping[str, Any], error_code: str) -> str:
        assert "source" not in inputs
        assert error_code == "WORKFLOW_DEPENDENCY_FAILED"
        return "local fallback"

    execution = await WorkflowGraph(
        [
            WorkflowNode("source", (), 1.0, fail),
            WorkflowNode("consumer", ("source",), 1.0, _value, degrade),
        ]
    ).execute({"request_id": "req-1"})

    assert execution.outcomes["consumer"] == NodeOutcome(
        status="degraded",
        output="local fallback",
        error_code="WORKFLOW_DEPENDENCY_FAILED",
    )


@pytest.mark.asyncio
async def test_blocked_node_skips_before_strict_input_validation() -> None:
    async def fail(_inputs: _EmptyInput) -> _IntegerOutput:
        raise RuntimeError("unavailable")

    async def healthy(_inputs: _EmptyInput) -> _IntegerOutput:
        return _IntegerOutput(value=7)

    execution = await WorkflowGraph(
        [
            WorkflowNode(
                "failed",
                (),
                1.0,
                fail,
                input_model=_EmptyInput,
                output_model=_IntegerOutput,
            ),
            WorkflowNode(
                "healthy",
                (),
                1.0,
                healthy,
                input_model=_EmptyInput,
                output_model=_IntegerOutput,
            ),
            WorkflowNode(
                "consumer",
                ("failed", "healthy"),
                1.0,
                lambda _inputs: _SeenOutput(marker="unreachable"),
                input_model=_StrictDependencyInput,
                output_model=_SeenOutput,
            ),
        ]
    ).execute({})

    assert execution.outcomes["consumer"] == NodeOutcome(
        status="skipped",
        error_code="WORKFLOW_DEPENDENCY_FAILED",
    )
    assert set(execution.inputs_seen_by("consumer")) == {"healthy"}


@pytest.mark.asyncio
async def test_blocked_node_degrades_with_typed_partial_usable_inputs() -> None:
    degraded_inputs: list[_PartialHealthyInput] = []

    async def fail(_inputs: _EmptyInput) -> _IntegerOutput:
        raise RuntimeError("unavailable")

    async def healthy(_inputs: _EmptyInput) -> _IntegerOutput:
        return _IntegerOutput(value=11)

    async def degrade(
        inputs: _PartialHealthyInput,
        error_code: str,
    ) -> _SeenOutput:
        degraded_inputs.append(inputs)
        assert error_code == "WORKFLOW_DEPENDENCY_FAILED"
        return _SeenOutput(marker=str(inputs.healthy.value))

    execution = await WorkflowGraph(
        [
            WorkflowNode(
                "failed",
                (),
                1.0,
                fail,
                input_model=_EmptyInput,
                output_model=_IntegerOutput,
            ),
            WorkflowNode(
                "healthy",
                (),
                1.0,
                healthy,
                input_model=_EmptyInput,
                output_model=_IntegerOutput,
            ),
            WorkflowNode(
                "consumer",
                ("failed", "healthy"),
                1.0,
                lambda _inputs: _SeenOutput(marker="unreachable"),
                degrade=degrade,
                input_model=_StrictDependencyInput,
                output_model=_SeenOutput,
                degrade_input_model=_PartialHealthyInput,
            ),
        ]
    ).execute({})

    assert len(degraded_inputs) == 1
    assert degraded_inputs[0].healthy.value == 11
    assert execution.outcomes["consumer"] == NodeOutcome(
        status="degraded",
        output={"marker": "11"},
        error_code="WORKFLOW_DEPENDENCY_FAILED",
    )


@pytest.mark.asyncio
async def test_node_inputs_and_execution_outcomes_are_immutable_copies() -> None:
    initial = {"request": {"tags": ["original"]}}
    source_output = {"items": ["one"]}

    async def source(inputs: Mapping[str, Any]) -> dict[str, list[str]]:
        with pytest.raises(TypeError):
            inputs["request"]["new"] = "mutation"
        return source_output

    async def consumer(inputs: Mapping[str, Any]) -> str:
        with pytest.raises(AttributeError):
            inputs["source"]["items"].append("two")
        return "unchanged"

    execution = await WorkflowGraph(
        [
            WorkflowNode("source", (), 1.0, source),
            WorkflowNode("consumer", ("source",), 1.0, consumer),
        ]
    ).execute(initial)
    initial["request"]["tags"].append("caller mutation")
    source_output["items"].append("producer mutation")

    assert execution.outcomes["source"].output["items"] == ("one",)
    with pytest.raises(TypeError):
        execution.outcomes["new"] = NodeOutcome(status="skipped")
    with pytest.raises(FrozenInstanceError):
        execution.outcomes["source"].status = "failed"


@pytest.mark.asyncio
async def test_outcome_order_is_deterministic_not_completion_order() -> None:
    async def delayed(delay: float, value: str) -> str:
        await asyncio.sleep(delay)
        return value

    graph = WorkflowGraph(
        [
            WorkflowNode("root", (), 1.0, lambda _inputs: _value("root")),
            WorkflowNode("slow", ("root",), 1.0, lambda _inputs: delayed(0.02, "slow")),
            WorkflowNode("fast", ("root",), 1.0, lambda _inputs: delayed(0.001, "fast")),
        ]
    )

    first = await graph.execute({})
    second = await graph.execute({})

    assert list(first.outcomes) == ["root", "slow", "fast"]
    assert list(second.outcomes) == ["root", "slow", "fast"]


@pytest.mark.asyncio
async def test_cancelling_execution_cleans_up_all_running_siblings() -> None:
    started = {"left": asyncio.Event(), "right": asyncio.Event()}
    cleaned = {"left": asyncio.Event(), "right": asyncio.Event()}

    def blocking(name: str):
        async def run(_inputs: Mapping[str, Any]) -> None:
            started[name].set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned[name].set()

        return run

    graph = WorkflowGraph(
        [
            WorkflowNode("left", (), 30.0, blocking("left")),
            WorkflowNode("right", (), 30.0, blocking("right")),
        ]
    )
    execution_task = asyncio.create_task(graph.execute({}))
    await asyncio.gather(*(event.wait() for event in started.values()))

    execution_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution_task

    assert all(event.is_set() for event in cleaned.values())


@pytest.mark.asyncio
async def test_wrong_input_schema_fails_with_stable_code() -> None:
    secret = "SECRET_INPUT_VALIDATION_DETAIL"

    async def source(_inputs: _EmptyInput) -> dict[str, str]:
        return {"value": secret}

    async def consumer(_inputs: _IntegerInput) -> _SeenOutput:
        return _SeenOutput(marker="unreachable")

    execution = await WorkflowGraph(
        [
            WorkflowNode(
                "source",
                (),
                1.0,
                source,
                input_model=_EmptyInput,
            ),
            WorkflowNode(
                "consumer",
                ("source",),
                1.0,
                consumer,
                input_model=_IntegerInput,
                output_model=_SeenOutput,
            ),
        ]
    ).execute({})

    assert execution.outcomes["consumer"].status == "failed"
    assert (
        execution.outcomes["consumer"].error_code
        == "WORKFLOW_INPUT_VALIDATION_FAILED"
    )
    assert secret not in repr(execution.outcomes["consumer"])


@pytest.mark.asyncio
async def test_wrong_output_schema_fails_with_stable_code() -> None:
    secret = "SECRET_OUTPUT_VALIDATION_DETAIL"

    async def invalid(_inputs: _EmptyInput) -> dict[str, str]:
        return {"value": secret}

    execution = await WorkflowGraph(
        [
            WorkflowNode(
                "invalid",
                (),
                1.0,
                invalid,
                input_model=_EmptyInput,
                output_model=_IntegerOutput,
            )
        ]
    ).execute({})

    assert execution.outcomes["invalid"].status == "failed"
    assert (
        execution.outcomes["invalid"].error_code
        == "WORKFLOW_OUTPUT_VALIDATION_FAILED"
    )
    assert secret not in repr(execution.outcomes["invalid"])


@pytest.mark.asyncio
async def test_outcome_contains_deeply_immutable_resource_snapshot() -> None:
    resource = Resource(
        resource_id="resource-immutable",
        type=ResourceType.DOCUMENT,
        title="Immutable",
        content="content",
        metadata={"nested": {"items": ["original"]}},
    )

    async def source(_inputs: _EmptyInput) -> _ResourceOutput:
        return _ResourceOutput(resource=resource)

    execution = await WorkflowGraph(
        [
            WorkflowNode(
                "source",
                (),
                1.0,
                source,
                input_model=_EmptyInput,
                output_model=_ResourceOutput,
            )
        ]
    ).execute({})
    resource.metadata["nested"]["items"].append("producer mutation")
    snapshot = execution.outcomes["source"].output

    assert snapshot["resource"]["metadata"]["nested"]["items"] == ("original",)
    with pytest.raises(TypeError):
        snapshot["resource"]["metadata"]["new"] = "mutation"
    with pytest.raises(AttributeError):
        snapshot["resource"]["metadata"]["nested"]["items"].append("mutation")


@pytest.mark.asyncio
async def test_siblings_receive_independent_typed_resource_clones() -> None:
    mutated = asyncio.Event()

    async def source(_inputs: _EmptyInput) -> _ResourceOutput:
        return _ResourceOutput(
            resource=Resource(
                resource_id="resource-clone",
                type=ResourceType.DOCUMENT,
                title="Clone",
                content="content",
                metadata={"marker": "original"},
            )
        )

    async def mutator(inputs: _ResourceInput) -> _SeenOutput:
        inputs.source.resource.metadata["marker"] = "mutated"
        mutated.set()
        return _SeenOutput(marker=inputs.source.resource.metadata["marker"])

    async def observer(inputs: _ResourceInput) -> _SeenOutput:
        await mutated.wait()
        return _SeenOutput(marker=inputs.source.resource.metadata["marker"])

    graph = WorkflowGraph(
        [
            WorkflowNode(
                "source",
                (),
                1.0,
                source,
                input_model=_EmptyInput,
                output_model=_ResourceOutput,
            ),
            WorkflowNode(
                "mutator",
                ("source",),
                1.0,
                mutator,
                input_model=_ResourceInput,
                output_model=_SeenOutput,
            ),
            WorkflowNode(
                "observer",
                ("source",),
                1.0,
                observer,
                input_model=_ResourceInput,
                output_model=_SeenOutput,
            ),
        ]
    )
    execution = await graph.execute({})

    assert execution.outcomes["mutator"].output["marker"] == "mutated"
    assert execution.outcomes["observer"].output["marker"] == "original"
    assert (
        execution.outcomes["source"].output["resource"]["metadata"]["marker"]
        == "original"
    )
