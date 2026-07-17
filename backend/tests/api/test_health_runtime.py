from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from tutor.api import main as main_module
from tutor.api.routers import health as health_module
from tutor.services.config.settings import Settings


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
