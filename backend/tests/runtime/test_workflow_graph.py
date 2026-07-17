from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from time import perf_counter
from typing import Any

import pytest
from tutor.runtime.workflow_graph import (
    NodeOutcome,
    WorkflowGraph,
    WorkflowNode,
)


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
