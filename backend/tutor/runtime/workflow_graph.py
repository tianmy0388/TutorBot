"""Typed, deterministic DAG execution for capability-internal workflows.

The graph deliberately has no job or stream terminal semantics.  It only
coordinates capability nodes and returns immutable outcomes to its caller.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, is_dataclass
from enum import Enum
from pathlib import Path
from time import perf_counter
from types import MappingProxyType
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

TIn = TypeVar("TIn")
TOut = TypeVar("TOut")

NodeStatus = Literal["succeeded", "failed", "degraded", "skipped"]
NodeRun = Callable[[TIn], Awaitable[TOut] | TOut]
NodeDegrade = Callable[
    [TIn, str],
    Awaitable[TOut] | TOut,
]


@dataclass(frozen=True)
class WorkflowNode(Generic[TIn, TOut]):
    """One explicitly named node and its direct dependencies."""

    name: str
    dependencies: tuple[str, ...]
    timeout_seconds: float
    run: NodeRun[TIn, TOut]
    degrade: NodeDegrade[TIn, TOut] | None = None
    input_model: Any | None = None
    output_model: Any | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "dependencies", tuple(self.dependencies))
        if not self.name:
            raise ValueError("workflow node name must not be empty")
        if self.timeout_seconds <= 0:
            raise ValueError(f"workflow node {self.name!r} timeout_seconds must be positive")


@dataclass(frozen=True)
class NodeOutcome(Generic[TOut]):
    """Public, exception-free result of one workflow node."""

    status: NodeStatus
    output: TOut | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class WorkflowExecution:
    """Immutable execution snapshot in graph declaration order."""

    outcomes: Mapping[str, NodeOutcome[Any]]
    elapsed_seconds: float
    node_inputs: Mapping[str, Mapping[str, Any]]

    def inputs_seen_by(self, node_name: str) -> Mapping[str, Any]:
        """Return the frozen input snapshot passed to ``node_name``."""

        return self.node_inputs[node_name]


class WorkflowGraph:
    """Validate and execute a fixed directed acyclic graph."""

    def __init__(self, nodes: Iterable[WorkflowNode[Any, Any]]) -> None:
        declared = tuple(nodes)
        names: set[str] = set()
        for node in declared:
            if node.name in names:
                raise ValueError(f"duplicate node name: {node.name}")
            names.add(node.name)

        for node in declared:
            for dependency in node.dependencies:
                if dependency not in names:
                    raise ValueError(f"missing dependency {dependency!r} for node {node.name!r}")

        self._validate_acyclic(declared)
        self.nodes = declared

    def typed_output(
        self,
        execution: WorkflowExecution,
        node_name: str,
    ) -> Any:
        """Reconstruct a private typed clone from an immutable outcome."""

        node = next((item for item in self.nodes if item.name == node_name), None)
        if node is None:
            raise KeyError(node_name)
        outcome = execution.outcomes[node_name]
        if outcome.status not in {"succeeded", "degraded"}:
            raise ValueError(f"workflow node {node_name!r} has no usable output")
        raw = _clone(outcome.output)
        if node.output_model is None:
            return raw
        return _adapter(node.output_model).validate_python(raw)

    @staticmethod
    def _validate_acyclic(nodes: tuple[WorkflowNode[Any, Any], ...]) -> None:
        remaining = {node.name: set(node.dependencies) for node in nodes}
        resolved: set[str] = set()
        while remaining:
            ready = [
                node.name
                for node in nodes
                if node.name in remaining and remaining[node.name].issubset(resolved)
            ]
            if not ready:
                cycle_nodes = ", ".join(node.name for node in nodes if node.name in remaining)
                raise ValueError(f"workflow graph contains a cycle: {cycle_nodes}")
            resolved.update(ready)
            for name in ready:
                del remaining[name]

    async def execute(self, initial: dict[str, Any]) -> WorkflowExecution:
        """Execute each ready topological layer and isolate node failures."""

        started = perf_counter()
        frozen_initial = _freeze(deepcopy(initial))
        if not isinstance(frozen_initial, Mapping):  # pragma: no cover - dict input
            raise TypeError("initial workflow input must be a mapping")

        completed: dict[str, NodeOutcome[Any]] = {}
        input_snapshots: dict[str, Mapping[str, Any]] = {}
        pending = {node.name for node in self.nodes}

        while pending:
            ready = [
                node
                for node in self.nodes
                if node.name in pending and all(dependency in completed for dependency in node.dependencies)
            ]
            if not ready:  # Construction validation makes this unreachable.
                raise RuntimeError("workflow graph execution stalled")

            tasks: dict[str, asyncio.Task[NodeOutcome[Any]]] = {}
            async with asyncio.TaskGroup() as task_group:
                for node in ready:
                    tasks[node.name] = task_group.create_task(
                        self._execute_node(
                            node,
                            frozen_initial,
                            completed,
                            input_snapshots,
                        )
                    )

            # Commit a completed layer in declaration order, never task finish order.
            for node in ready:
                completed[node.name] = tasks[node.name].result()
                pending.remove(node.name)

        ordered_outcomes = MappingProxyType({node.name: completed[node.name] for node in self.nodes})
        ordered_inputs = MappingProxyType(
            {node.name: input_snapshots[node.name] for node in self.nodes if node.name in input_snapshots}
        )
        return WorkflowExecution(
            outcomes=ordered_outcomes,
            elapsed_seconds=perf_counter() - started,
            node_inputs=ordered_inputs,
        )

    async def _execute_node(
        self,
        node: WorkflowNode[Any, Any],
        initial: Mapping[str, Any],
        completed: Mapping[str, NodeOutcome[Any]],
        input_snapshots: dict[str, Mapping[str, Any]],
    ) -> NodeOutcome[Any]:
        usable_dependencies = {
            dependency: completed[dependency].output
            for dependency in node.dependencies
            if completed[dependency].status in {"succeeded", "degraded"}
        }
        inputs = _freeze(
            {
                **_clone(initial),
                **_clone(usable_dependencies),
            }
        )
        if not isinstance(inputs, Mapping):  # pragma: no cover - built as dict
            raise TypeError("workflow node inputs must be a mapping")
        input_snapshots[node.name] = inputs

        try:
            typed_inputs = (
                _adapter(node.input_model).validate_python(_clone(inputs))
                if node.input_model is not None
                else inputs
            )
        except ValidationError:
            return NodeOutcome(
                status="failed",
                error_code="WORKFLOW_INPUT_VALIDATION_FAILED",
            )

        blocked = any(
            completed[dependency].status in {"failed", "skipped"} for dependency in node.dependencies
        )
        if blocked:
            if node.degrade is None:
                return NodeOutcome(
                    status="skipped",
                    error_code="WORKFLOW_DEPENDENCY_FAILED",
                )
            return await self._degrade(
                node,
                typed_inputs,
                "WORKFLOW_DEPENDENCY_FAILED",
            )

        try:
            async with asyncio.timeout(node.timeout_seconds):
                output = await _resolve(node.run(typed_inputs))
        except TimeoutError:
            if node.degrade is None:
                return NodeOutcome(
                    status="failed",
                    error_code="WORKFLOW_NODE_TIMEOUT",
                )
            return await self._degrade(node, typed_inputs, "WORKFLOW_NODE_TIMEOUT")
        except Exception:  # noqa: BLE001 - exceptions stay private to the graph
            if node.degrade is None:
                return NodeOutcome(
                    status="failed",
                    error_code="WORKFLOW_NODE_FAILED",
                )
            return await self._degrade(node, typed_inputs, "WORKFLOW_NODE_FAILED")

        try:
            snapshot = _validated_snapshot(output, node.output_model)
        except ValidationError:
            return NodeOutcome(
                status="failed",
                error_code="WORKFLOW_OUTPUT_VALIDATION_FAILED",
            )
        return NodeOutcome(status="succeeded", output=snapshot)

    @staticmethod
    async def _degrade(
        node: WorkflowNode[Any, Any],
        inputs: Any,
        cause_code: str,
    ) -> NodeOutcome[Any]:
        if node.degrade is None:  # pragma: no cover - guarded by caller
            return NodeOutcome(status="failed", error_code=cause_code)
        try:
            async with asyncio.timeout(node.timeout_seconds):
                output = await _resolve(node.degrade(inputs, cause_code))
        except TimeoutError:
            return NodeOutcome(
                status="failed",
                error_code="WORKFLOW_DEGRADATION_TIMEOUT",
            )
        except Exception:  # noqa: BLE001 - provider details must never escape
            return NodeOutcome(
                status="failed",
                error_code="WORKFLOW_DEGRADATION_FAILED",
            )
        try:
            snapshot = _validated_snapshot(output, node.output_model)
        except ValidationError:
            return NodeOutcome(
                status="failed",
                error_code="WORKFLOW_OUTPUT_VALIDATION_FAILED",
            )
        return NodeOutcome(status="degraded", output=snapshot, error_code=cause_code)


async def _resolve(value: Awaitable[TOut] | TOut) -> TOut:
    if inspect.isawaitable(value):
        return await value
    return value


def _freeze(value: Any) -> Any:
    """Create a deeply immutable JSON-like graph-boundary snapshot."""

    if isinstance(value, BaseModel):
        return _freeze(value.model_dump(mode="json", by_alias=True))
    if is_dataclass(value) and not isinstance(value, type):
        return _freeze(TypeAdapter(type(value)).dump_python(value, mode="json"))
    if isinstance(value, Enum):
        return _freeze(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _adapter(schema: Any) -> TypeAdapter[Any]:
    return schema if isinstance(schema, TypeAdapter) else TypeAdapter(schema)


def _validated_snapshot(value: Any, schema: Any | None) -> Any:
    if schema is None:
        return _freeze(deepcopy(value))
    adapter = _adapter(schema)
    validated = adapter.validate_python(value)
    return _freeze(adapter.dump_python(validated, mode="json", by_alias=True))


def _clone(value: Any) -> Any:
    """Copy frozen containers without asking ``deepcopy`` to pickle proxies."""

    if isinstance(value, Mapping):
        return {key: _clone(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_clone(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(_clone(item) for item in value)
    return deepcopy(value)


__all__ = [
    "NodeOutcome",
    "WorkflowExecution",
    "WorkflowGraph",
    "WorkflowNode",
]
