from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from tutor.agents.resource.intent_understanding import Intent
from tutor.capabilities.resource_generation import ResourceGenerationCapability
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.resource_package.schema import Resource, ResourcePackage, ResourceType


class _PptAgent:
    def __init__(self, resource: Resource) -> None:
        self.resource = resource

    async def process(self, **kwargs) -> Resource:  # noqa: ARG002
        return self.resource


@pytest.mark.asyncio
async def test_ppt_resource_and_final_package_stream_only_portable_keys(
    tmp_path, monkeypatch
) -> None:
    import tutor.services.ppt as ppt_module
    from tutor.services.config.settings import reset_settings_cache

    data_dir = tmp_path / "data"
    source = data_dir / "ppt" / "ad_hoc" / "deck.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"pptx")
    monkeypatch.setenv("TUTOR_DATA_DIR", str(data_dir))
    reset_settings_cache()
    monkeypatch.setattr(
        ppt_module,
        "get_ppt_service",
        lambda: SimpleNamespace(output_dir=data_dir / "ppt"),
    )

    resource = Resource(
        resource_id="ppt-resource",
        type=ResourceType.PPT,
        title="Deck",
        format_specific={"pptx_path": str(source), "slide_count": 1},
    )
    capability = ResourceGenerationCapability.__new__(ResourceGenerationCapability)
    capability.ppt_generator = _PptAgent(resource)
    stream = StreamBus()
    queue = stream.subscribe()

    resources = await capability._generate_parallel(
        context=UnifiedContext(language="zh"),
        intent=Intent(topic="Deck", resource_types=[ResourceType.PPT]),
        profile_snapshot={},
        source_content="# Deck",
        planned_types=[ResourceType.PPT],
        stream=stream,
    )
    incremental = await queue.get()
    while incremental.type.value != "resource":
        incremental = await queue.get()
    incremental_fs = incremental.metadata["resource"]["format_specific"]
    assert incremental_fs["artifact_key"] == "ppt/ad_hoc/deck.pptx"
    assert "pptx_path" not in incremental_fs
    assert str(data_dir) not in json.dumps(incremental.metadata)

    package = ResourcePackage(
        package_id="package-ppt",
        topic="Deck",
        resources=resources,
    )
    capability._relocate_ppt_artifacts(package)
    await stream.result({"package": package.model_dump(mode="json")})
    final = await queue.get()
    while final.type.value != "result":
        final = await queue.get()
    final_payload = json.loads(final.content)
    final_fs = final_payload["package"]["resources"][0]["format_specific"]
    assert final_fs["artifact_key"] == "ppt/package-ppt/deck.pptx"
    assert "pptx_path" not in final_fs
    assert str(data_dir) not in final.content
    assert (data_dir / "ppt" / "package-ppt" / "deck.pptx").is_file()
