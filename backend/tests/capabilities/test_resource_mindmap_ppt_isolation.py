from __future__ import annotations

from typing import Any

import pytest
from tutor.capabilities.resource_generation import ResourceGenerationCapability
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.contracts import (
    ResourceIntentNodeOutput,
    ResourcePedagogyNodeOutput,
    ResourceProfileNodeOutput,
    ResourceSourceNodeOutput,
)
from tutor.services.resource_package.schema import Resource, ResourceType


class _ArtifactAgent:
    def __init__(self, resource: Resource | None = None, *, fail: bool = False) -> None:
        self.resource = resource
        self.fail = fail

    async def process(self, *args: Any, **kwargs: Any) -> Resource:
        if self.fail:
            raise RuntimeError("private provider detail")
        assert self.resource is not None
        return self.resource


def _resource(resource_type: ResourceType, resource_id: str) -> Resource:
    return Resource(
        resource_id=resource_id,
        type=resource_type,
        title=f"{resource_type.value} title",
        content=f"{resource_type.value} content",
        format_specific={
            "mermaid_dsl": "mindmap\n root((topic))"
        }
        if resource_type == ResourceType.MINDMAP
        else {"artifact_key": f"ppt/ad_hoc/{resource_id}.pptx"},
    )


def _pedagogy_output() -> ResourcePedagogyNodeOutput:
    intent = ResourceIntentNodeOutput(
        topic="topic",
        scope="single_concept",
        resource_types=("mindmap", "ppt"),
    )
    profile = ResourceProfileNodeOutput(intent=intent, profile_snapshot={})
    source_resource = Resource(
        resource_id="source",
        type=ResourceType.DOCUMENT,
        title="source",
        content="source content",
    )
    source = ResourceSourceNodeOutput(
        profile=profile,
        planned_types=("mindmap", "ppt"),
        source_resource=source_resource,
    )
    return ResourcePedagogyNodeOutput(
        source=source,
        pedagogy_resource=source_resource,
    )


async def _run_branch(*, mindmap_fail: bool, ppt_fail: bool):
    capability = ResourceGenerationCapability.__new__(ResourceGenerationCapability)
    capability.multimedia = _ArtifactAgent(
        _resource(ResourceType.MINDMAP, "mindmap-ok"),
        fail=mindmap_fail,
    )
    capability.ppt_generator = _ArtifactAgent(
        _resource(ResourceType.PPT, "ppt-ok"),
        fail=ppt_fail,
    )
    stream = StreamBus()
    queue = stream.subscribe()
    output = await capability._run_resource_branch(
        "mindmap",
        _pedagogy_output(),
        UnifiedContext(user_id="u", user_message="topic"),
        stream,
    )
    emitted_types = []
    while not queue.empty():
        event = queue.get_nowait()
        if event is not None and event.type.value == "resource":
            emitted_types.append(event.metadata["resource_type"])
    return output, emitted_types


@pytest.mark.asyncio
async def test_mindmap_failure_keeps_successful_ppt_artifact() -> None:
    output, emitted = await _run_branch(mindmap_fail=True, ppt_fail=False)

    assert [resource.type for resource in output.resources] == [ResourceType.PPT]
    assert emitted == ["ppt"]


@pytest.mark.asyncio
async def test_ppt_failure_keeps_successful_mindmap_artifact() -> None:
    output, emitted = await _run_branch(mindmap_fail=False, ppt_fail=True)

    assert [resource.type for resource in output.resources] == [ResourceType.MINDMAP]
    assert emitted == ["mindmap"]


@pytest.mark.asyncio
async def test_mindmap_and_ppt_success_keep_both_artifacts() -> None:
    output, emitted = await _run_branch(mindmap_fail=False, ppt_fail=False)

    assert [resource.type for resource in output.resources] == [
        ResourceType.MINDMAP,
        ResourceType.PPT,
    ]
    assert emitted == ["mindmap", "ppt"]
