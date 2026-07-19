from __future__ import annotations

import json

import pytest
from tutor.agents.resource.manim_repair import ManimRepairAgent
from tutor.core.context import UnifiedContext
from tutor.services.llm.base import LLMResponse
from tutor.services.manim_render.executor import RenderFailure

SOURCE = """from manim import *

class MainScene(Scene):
    def construct(self):
        dot = Dot()
        self.play(Create(dot), run_time=0)
"""

REPAIRED = """from manim import *

class MainScene(Scene):
    def construct(self):
        dot = Dot()
        self.play(Create(dot), run_time=0.5)
"""


class FakeLLM:
    model = "fake"

    def __init__(self, code: str = REPAIRED) -> None:
        self.code = code
        self.requests = []

    async def call(self, request):
        self.requests.append(request)
        return LLMResponse(
            content=json.dumps({"manim_code": self.code}),
            model=self.model,
            finish_reason="stop",
        )


@pytest.mark.asyncio
async def test_repair_prompt_contains_full_source_and_sanitised_failure():
    llm = FakeLLM()
    agent = ManimRepairAgent(llm=llm)  # type: ignore[arg-type]
    failure = RenderFailure(
        error_code="process_exit",
        summary="Manim exited before producing a video",
        traceback_tail=(
            'File "C:\\private\\render.py", line 9',
            "provider-token=private-value",
            "ValueError: run_time must be positive",
        ),
    )
    runtime = {"python": "3.11.9", "manim": "0.20.0"}

    repaired = await agent.regenerate(
        UnifiedContext(language="zh", user_message="repair"),
        failed_code=SOURCE,
        failure=failure,
        runtime=runtime,
    )

    prompt = llm.requests[-1].messages[-1].content
    assert SOURCE in prompt
    assert failure.error_code in prompt
    assert "python=3.11.9" in prompt
    assert "manim=0.20.0" in prompt
    assert "ValueError: run_time must be positive" in prompt
    assert "C:\\private" not in prompt
    assert "private-value" not in prompt
    assert "0.5.5" not in repaired
    assert repaired == REPAIRED


@pytest.mark.asyncio
async def test_repair_requires_one_complete_main_scene_json_field():
    llm = FakeLLM("class OtherScene: pass")
    agent = ManimRepairAgent(llm=llm)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="MainScene"):
        await agent.regenerate(
            UnifiedContext(language="zh"),
            failed_code=SOURCE,
            failure=RenderFailure("process_exit", "failed"),
            runtime={"python": "3.11"},
        )


@pytest.mark.asyncio
async def test_repair_requires_main_scene_to_inherit_scene():
    llm = FakeLLM(
        "class MainScene:\n    def construct(self):\n        pass\n"
    )
    agent = ManimRepairAgent(llm=llm)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="MainScene"):
        await agent.regenerate(
            UnifiedContext(language="zh"),
            failed_code=SOURCE,
            failure=RenderFailure("process_exit", "failed"),
            runtime={"python": "3.11"},
        )


def test_repair_prompt_requests_full_json_source_not_search_replace():
    agent = ManimRepairAgent(llm=FakeLLM())  # type: ignore[arg-type]
    prompt = agent.get_system_prompt(agent.get_prompt_data("zh"))

    assert "manim_code" in prompt
    assert "MainScene" in prompt
    assert "SEARCH/REPLACE" in prompt
    assert "不得" in prompt
