from __future__ import annotations

import concurrent.futures
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
