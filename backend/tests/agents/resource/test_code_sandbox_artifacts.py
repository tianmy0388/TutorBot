from __future__ import annotations

import concurrent.futures
import subprocess
import sys
from pathlib import Path

from tutor.agents.resource import code_sandbox
from tutor.agents.resource.code_sandbox import _safe_run_python
from tutor.services.config.settings import Settings


def _run(code: str, settings: Settings):
    return _safe_run_python(
        code,
        interpreter=settings.execution_python or sys.executable,
        timeout=30,
        settings=settings,
    )


def test_repeated_runs_share_the_exact_persistent_matplotlib_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    def forbidden_global_settings():
        raise AssertionError("_safe_run_python must use its explicit settings")

    monkeypatch.setattr(code_sandbox, "get_settings", forbidden_global_settings)
    code = (
        "import os\n"
        "print(os.environ['MPLCONFIGDIR'])\n"
        "import matplotlib.pyplot as plt\n"
        "plt.plot([1, 2])\n"
    )

    first = _run(code, settings)
    second = _run(code, settings)
    expected = str((tmp_path / "cache" / "matplotlib").resolve())

    assert first[0] == second[0] == "success"
    assert first[1].strip() == second[1].strip() == expected
    assert "Matplotlib is building the font cache" not in second[2]
    assert Path(expected).is_dir()


def test_repeated_runs_warm_dependency_probe_once_with_shared_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    calls: list[dict[str, str]] = []

    def probe(_interpreter: str, *, env: dict[str, str] | None = None):
        assert env is not None
        calls.append(env)
        return {"python": "test", "matplotlib": "test", "numpy": "test"}

    code_sandbox._DEPENDENCY_PROBE_CACHE.clear()
    monkeypatch.setattr(code_sandbox, "_probe_dependency_versions", probe)

    first = _run("print('first')", settings)
    second = _run("print('second')", settings)

    assert first[0] == second[0] == "success"
    assert len(calls) == 1
    assert calls[0]["MPLBACKEND"] == "Agg"
    assert calls[0]["MPLCONFIGDIR"] == str(
        (tmp_path / "cache" / "matplotlib").resolve()
    )


def test_concurrent_runs_share_only_cache_and_keep_artifacts_run_private(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    monkeypatch.setattr(code_sandbox, "get_settings", lambda: settings)
    codes = [
        "import matplotlib.pyplot as plt\nplt.plot([1, 2])",
        "import matplotlib.pyplot as plt\nplt.scatter([3], [4])",
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda code: _run(code, settings), codes))

    assert all(result[0] == "success" for result in results)
    artifact_keys = [result[5][0]["artifact_key"] for result in results]
    assert artifact_keys[0] != artifact_keys[1]
    assert all(key.endswith("/figure_1.png") for key in artifact_keys)
    assert all(not Path(key).is_absolute() for key in artifact_keys)
    assert all("cache/matplotlib" not in key.replace("\\", "/") for key in artifact_keys)


def test_unrelated_user_warnings_are_preserved(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    monkeypatch.setattr(code_sandbox, "get_settings", lambda: settings)
    result = _run(
        """
import warnings
import matplotlib.pyplot as plt
warnings.warn('keep this educational warning', RuntimeWarning)
warnings.warn('FigureCanvasAgg is interactive enough', UserWarning)
plt.plot([1, 2])
plt.show()
""",
        settings,
    )

    assert result[0] == "success"
    assert "keep this educational warning" in result[2]
    assert "FigureCanvasAgg is interactive enough" in result[2]
    assert "FigureCanvasAgg is non-interactive" not in result[2]


def test_artifact_keys_are_portable_and_never_expose_scratch_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    monkeypatch.setattr(code_sandbox, "get_settings", lambda: settings)
    result = _run(
        "import os\n"
        "print(os.getcwd())\n"
        "import matplotlib.pyplot as plt\n"
        "plt.plot([1, 2])",
        settings,
    )

    artifact = result[5][0]
    assert set(artifact) == {"name", "artifact_key", "kind"}
    assert not Path(artifact["artifact_key"]).is_absolute()
    assert str(tmp_path) not in artifact["artifact_key"]
    assert str(tmp_path) not in result[1]
    assert str(tmp_path) not in result[2]
    assert result[1].strip() == "<sandbox>"


def test_mixed_user_artifact_names_do_not_break_natural_manifest_sort(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    monkeypatch.setattr(code_sandbox, "get_settings", lambda: settings)
    result = _run(
        "from pathlib import Path\n"
        "Path('1.png').write_bytes(b'one')\n"
        "Path('alpha.png').write_bytes(b'alpha')\n",
        settings,
    )

    assert result[0] == "success"
    assert {artifact["name"] for artifact in result[5]} == {"1.png", "alpha.png"}


def test_many_captured_figures_keep_natural_manifest_order(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    monkeypatch.setattr(code_sandbox, "get_settings", lambda: settings)
    result = _run(
        "import matplotlib.pyplot as plt\n"
        "for value in range(12):\n"
        "    plt.figure(); plt.plot([value, value + 1])\n"
        "plt.show()\n",
        settings,
    )

    assert result[0] == "success"
    assert [artifact["name"] for artifact in result[5]] == [
        f"figure_{index}.png" for index in range(1, 13)
    ]


def test_cache_path_collision_returns_typed_preparation_failure(
    tmp_path: Path,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    cache_parent = tmp_path / "cache"
    cache_parent.mkdir()
    (cache_parent / "matplotlib").write_text("not a directory", encoding="utf-8")

    result = _run("print('must not run')", settings)

    assert result[0] == "failed"
    assert result[3] == "CODE_RUNTIME_PREPARATION_FAILED"
    assert result[2] == "[code runtime preparation failed]"
    assert str(tmp_path) not in result[2]


def test_cache_permission_error_returns_typed_preparation_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    original_mkdir = Path.mkdir

    def deny_cache(path: Path, *args, **kwargs):
        if path.name == "matplotlib":
            raise PermissionError(f"private host path: {tmp_path}")
        return original_mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", deny_cache)
    result = _run("print('must not run')", settings)

    assert result[0] == "failed"
    assert result[3] == "CODE_RUNTIME_PREPARATION_FAILED"
    assert result[2] == "[code runtime preparation failed]"
    assert str(tmp_path) not in str(result)


def test_dependency_probe_failure_is_not_cached_and_next_attempt_recovers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cache_dir = (tmp_path / "cache" / "matplotlib").resolve()
    env = {"MPLBACKEND": "Agg", "MPLCONFIGDIR": str(cache_dir)}
    responses = [
        {"python": "unknown", "probe_error": "DEPENDENCY_PROBE_FAILED"},
        {"python": "3.11", "matplotlib": "3.11.0", "numpy": "2.3.1"},
    ]
    calls = 0

    def probe(_interpreter: str, *, env=None):
        nonlocal calls
        calls += 1
        return responses.pop(0)

    code_sandbox._DEPENDENCY_PROBE_CACHE.clear()
    monkeypatch.setattr(code_sandbox, "_probe_dependency_versions", probe)

    first = code_sandbox._cached_dependency_versions(
        sys.executable, matplotlib_cache=cache_dir, env=env
    )
    second = code_sandbox._cached_dependency_versions(
        sys.executable, matplotlib_cache=cache_dir, env=env
    )
    third = code_sandbox._cached_dependency_versions(
        sys.executable, matplotlib_cache=cache_dir, env=env
    )

    assert first["probe_error"] == "DEPENDENCY_PROBE_FAILED"
    assert second == third
    assert calls == 2


def test_dependency_probe_failure_never_returns_raw_child_output(
    tmp_path: Path,
    monkeypatch,
) -> None:
    secret = f"Traceback at {tmp_path} token=sk-private"

    def failed_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout=secret, stderr=secret)

    monkeypatch.setattr(code_sandbox.subprocess, "run", failed_run)
    result = code_sandbox._probe_dependency_versions(sys.executable, env={})

    assert result == {
        "python": "unknown",
        "probe_error": "DEPENDENCY_PROBE_FAILED",
    }
    assert str(tmp_path) not in str(result)
    assert "Traceback" not in str(result)
    assert "sk-private" not in str(result)


def test_dependency_import_error_is_a_stable_uncacheable_probe_failure(
    monkeypatch,
) -> None:
    payload = '{"python":"3.11","matplotlib":"error","numpy":"2.3"}'

    def completed_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=payload, stderr="")

    monkeypatch.setattr(code_sandbox.subprocess, "run", completed_run)
    result = code_sandbox._probe_dependency_versions(sys.executable, env={})

    assert result == {
        "python": "3.11",
        "probe_error": "DEPENDENCY_PROBE_FAILED",
    }
