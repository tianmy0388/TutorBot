from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from tutor.core.context import UnifiedContext
from tutor.runtime.orchestrator import MainOrchestrator
from tutor.services.jobs.runner import JobRunner
from tutor.services.jobs.schema import JobSubmit


class _Capabilities:
    def __init__(self) -> None:
        self._names = {
            "tutoring",
            "resource_generation",
            "assessment",
            "profile",
            "path_planning",
        }

    def get(self, name: str):
        return object() if name in self._names else None

    def list_capabilities(self) -> list[str]:
        return sorted(self._names)

    def get_manifests(self) -> list[dict[str, object]]:
        return []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "capability"),
    [
        ("解释一下注意力机制", "tutoring"),
        ("生成一份代码示例", "resource_generation"),
        ("给我做一次测验", "assessment"),
        ("查看我的学习画像", "profile"),
        ("下一步该学什么", "path_planning"),
    ],
)
async def test_submit_without_capability_uses_intent_router_once(
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    capability: str,
) -> None:
    import tutor.services.jobs.runner as runner_module
    from tutor.services.intent.router import classify

    store = AsyncMock()
    runner = JobRunner(job_store=store, capability_registry=_Capabilities())  # type: ignore[arg-type]
    monkeypatch.setattr(runner, "_schedule", lambda _job: None)
    route = Mock(wraps=classify)
    monkeypatch.setattr(runner_module, "classify", route)

    job = await runner.submit(JobSubmit(user_id="u1", message=message))

    assert job.capability == capability
    route.assert_called_once_with(message)
    store.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_preserves_explicit_capability_without_calling_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tutor.services.jobs.runner as runner_module

    store = AsyncMock()
    runner = JobRunner(job_store=store, capability_registry=_Capabilities())  # type: ignore[arg-type]
    monkeypatch.setattr(runner, "_schedule", lambda _job: None)
    route = Mock(side_effect=AssertionError("router must not run for explicit hints"))
    monkeypatch.setattr(runner_module, "classify", route, raising=False)

    job = await runner.submit(
        JobSubmit(
            user_id="u1",
            message="生成资源",
            capability="tutoring",
        )
    )

    assert job.capability == "tutoring"
    route.assert_not_called()


@pytest.mark.asyncio
async def test_submit_rejects_an_invalid_explicit_capability_without_rerouting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tutor.services.jobs.runner as runner_module

    store = AsyncMock()
    runner = JobRunner(job_store=store, capability_registry=_Capabilities())  # type: ignore[arg-type]
    route = Mock(side_effect=AssertionError("invalid explicit hints must not reroute"))
    monkeypatch.setattr(runner_module, "classify", route)

    with pytest.raises(ValueError, match="INVALID_CAPABILITY"):
        await runner.submit(
            JobSubmit(message="解释注意力机制", capability="not-a-capability")
        )

    route.assert_not_called()
    store.save.assert_not_awaited()


@pytest.mark.parametrize(
    "message",
    [
        "解释一下注意力机制",
        "生成一份代码示例",
        "给我做一次测验",
        "查看我的学习画像",
        "下一步该学什么",
    ],
)
def test_orchestrator_delegates_to_the_shared_router(message: str) -> None:
    from tutor.services.intent.router import classify

    orchestrator = MainOrchestrator(capability_registry=_Capabilities())  # type: ignore[arg-type]
    context = UnifiedContext(user_message=message)

    assert orchestrator.route(context) == classify(message).capability


def test_orchestrator_rejects_invalid_explicit_capability() -> None:
    orchestrator = MainOrchestrator(capability_registry=_Capabilities())  # type: ignore[arg-type]
    context = UnifiedContext(
        user_message="生成一份学习资源",
        capability="admin",
    )

    with pytest.raises(ValueError, match="INVALID_CAPABILITY"):
        orchestrator.route(context)


def test_orchestrator_preserves_valid_explicit_capability() -> None:
    orchestrator = MainOrchestrator(capability_registry=_Capabilities())  # type: ignore[arg-type]
    context = UnifiedContext(
        user_message="生成一份学习资源",
        capability="tutoring",
    )

    assert orchestrator.route(context) == "tutoring"


def test_orchestrator_preserves_valid_explicit_capability_without_message() -> None:
    orchestrator = MainOrchestrator(capability_registry=_Capabilities())  # type: ignore[arg-type]
    context = UnifiedContext(user_message="", capability="profile")

    assert orchestrator.route(context) == "profile"
