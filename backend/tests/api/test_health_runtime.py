from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from tutor.agents.resource.code_sandbox import CodeSandboxAgent
from tutor.api import main as main_module
from tutor.api.routers import health as health_module
from tutor.core.context import UnifiedContext
from tutor.services.config.settings import Settings
from tutor.services.llm.base import LLMResponse


def test_health_reports_injected_matplotlib_runtime_and_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    injected = Settings(
        env="test",
        data_dir=tmp_path / "injected",
        execution_python=sys.executable,
    )
    forbidden = Settings(
        env="test",
        data_dir=tmp_path / "forbidden-global",
        execution_python="missing-global-python",
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: forbidden)
    app = main_module.create_app(injected)

    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    runtime = body["runtime"]
    matplotlib = runtime["matplotlib"]
    assert body["status"] == "ok"
    assert body["readiness"] == "ready"
    assert runtime["execution_python"] == str(Path(sys.executable).resolve())
    assert matplotlib["status"] == "ok"
    assert matplotlib["version"]
    assert matplotlib["backend"].lower() == "agg"
    assert matplotlib["cache_dir"] == str(
        (injected.data_dir / "cache" / "matplotlib").resolve()
    )
    assert matplotlib["writable"] is True
    assert not forbidden.data_dir.exists()


def test_health_runtime_failure_is_stable_and_redacted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(
        env="test",
        data_dir=tmp_path / "data",
        execution_python=str(tmp_path / "missing-python.exe"),
    )

    def fail_probe(*args, **kwargs):
        raise RuntimeError("Traceback C:/private/secret.py sk-test-token")

    monkeypatch.setattr(subprocess, "run", fail_probe)
    diagnostic = health_module._matplotlib_runtime_diagnostics(settings)
    rendered = str(diagnostic)

    assert diagnostic["status"] == "unavailable"
    assert diagnostic["error_code"] == "MATPLOTLIB_RUNTIME_UNAVAILABLE"
    assert "Traceback" not in rendered
    assert "secret.py" not in rendered
    assert "sk-test-token" not in rendered
    assert set(diagnostic) == {
        "status",
        "version",
        "backend",
        "cache_dir",
        "writable",
        "error_code",
    }


def test_health_rejects_non_agg_backend_even_when_probe_exits_cleanly(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(
        env="test",
        data_dir=tmp_path / "data",
        execution_python=sys.executable,
    )
    cache_dir = (settings.data_dir / "cache" / "matplotlib").resolve()

    def tk_probe(*args, **kwargs):
        payload = {
            "version": "3.11.0",
            "backend": "TkAgg",
            "cache_dir": str(cache_dir),
        }
        return subprocess.CompletedProcess(args[0], 0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(health_module.subprocess, "run", tk_probe)
    diagnostic = health_module._matplotlib_runtime_diagnostics(settings)

    assert diagnostic["status"] == "unavailable"
    assert diagnostic["error_code"] == "MATPLOTLIB_RUNTIME_MISCONFIGURED"
    assert diagnostic["backend"] == "TkAgg"


def test_health_keeps_liveness_ok_but_reports_degraded_readiness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path / "data")
    app = main_module.create_app(settings)
    monkeypatch.setattr(
        health_module,
        "_matplotlib_runtime_diagnostics",
        lambda _settings: {
            "status": "unavailable",
            "version": None,
            "backend": None,
            "cache_dir": str(settings.data_dir / "cache" / "matplotlib"),
            "writable": False,
            "error_code": "MATPLOTLIB_RUNTIME_UNAVAILABLE",
        },
    )

    body = TestClient(app).get("/api/v1/health").json()

    assert body["status"] == "ok"
    assert body["readiness"] == "degraded"


def _mock_code_llm(code: str):
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(_request):
        return LLMResponse(
            content=json.dumps(
                {
                    "title": "Injected settings",
                    "language": "python",
                    "code": code,
                    "explanation": "settings ownership",
                }
            ),
            model="mock",
            finish_reason="stop",
        )

    llm.call = call
    return llm


@pytest.mark.asyncio
async def test_create_app_injected_settings_reach_real_code_sandbox_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    injected = Settings(
        env="test",
        data_dir=tmp_path / "injected",
        execution_python=sys.executable,
    )

    def forbidden_global_settings():
        raise AssertionError("application-owned code sandbox used global settings")

    monkeypatch.setattr(main_module, "get_settings", forbidden_global_settings)
    monkeypatch.setattr(
        "tutor.agents.resource.code_sandbox.get_settings",
        forbidden_global_settings,
    )
    app = main_module.create_app(injected)
    async with app.router.lifespan_context(app):
        capability = app.state.capabilities.get("resource_generation")
        assert app.state.learning_runner.capabilities.get(
            "resource_generation"
        ) is capability
        assert app.state.learning_runner._follow_up_builder(
            "video_render"
        )._settings is injected
        sandbox = capability.code_sandbox
        sandbox.llm = _mock_code_llm(
            "import os\n"
            "print(os.environ['MPLCONFIGDIR'])\n"
            "import matplotlib.pyplot as plt\n"
            "plt.plot([1, 2])\n"
        )
        resource = await sandbox.process(
            UnifiedContext(user_message="plot"),
            topic="plot",
        )

    payload = resource.format_specific
    assert payload["execution_python"] == sys.executable
    assert payload["stdout"].strip() == str(
        (injected.data_dir / "cache" / "matplotlib").resolve()
    )
    assert payload["artifacts"][0]["artifact_key"].startswith("code_runs/")
    assert (injected.data_dir / "cache" / "matplotlib").is_dir()


@pytest.mark.asyncio
async def test_agent_preparation_failure_returns_private_typed_resource(
    tmp_path: Path,
) -> None:
    settings = Settings(
        env="test",
        data_dir=tmp_path / "data",
        execution_python=sys.executable,
    )
    cache_parent = settings.data_dir / "cache"
    cache_parent.mkdir(parents=True)
    (cache_parent / "matplotlib").write_text("collision", encoding="utf-8")
    agent = CodeSandboxAgent(
        settings=settings,
        llm=_mock_code_llm("print('must not execute')"),
    )

    resource = await agent.process(UnifiedContext(user_message="run"), topic="run")

    payload = resource.format_specific
    assert payload["execution_status"] == "failed"
    assert payload["error_code"] == "CODE_RUNTIME_PREPARATION_FAILED"
    assert payload["stderr"] == "[code runtime preparation failed]"
    assert str(tmp_path) not in resource.content
    assert payload["failure"]["code"] == "CODE_RUNTIME_PREPARATION_FAILED"


@pytest.mark.asyncio
async def test_dependency_probe_failure_stays_private_in_resource_markdown(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "private-host" / "missing-python.exe"
    settings = Settings(
        env="test",
        data_dir=tmp_path / "data",
        execution_python=str(missing),
    )
    agent = CodeSandboxAgent(
        settings=settings,
        llm=_mock_code_llm("print('will not execute')"),
    )

    resource = await agent.process(UnifiedContext(user_message="run"), topic="run")

    payload = resource.format_specific
    assert payload["dependency_versions"] == {
        "python": "unknown",
        "probe_error": "DEPENDENCY_INTERPRETER_UNAVAILABLE",
    }
    assert str(tmp_path) not in resource.content
    assert "Traceback" not in resource.content
    assert "probe_stderr" not in str(payload["dependency_versions"])
