"""Regression: matplotlib figures that the user code leaves open
after ``plt.show()`` must end up as artifacts on the resource.

Pre-fix, the sandbox used ``MPLBACKEND=Agg`` so ``plt.show()`` was
a no-op that printed::

    UserWarning: FigureCanvasAgg is non-interactive, and thus
    cannot be shown

…leaving the figure only in memory. The artifact picker walked the
scratch dir and found nothing. The user's "反向传播训练XOR" snippet
hit exactly this: loss curve was drawn, ``plt.show()`` did nothing,
right pane was empty.

The fix drains all open figures to ``scratch/figure_N.png`` before
the artifact picker runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from tutor.agents.resource.code_sandbox import (
    CodeSandboxAgent,
    _code_uses_matplotlib,
    _safe_run_python,
    _wrap_user_code,
)
from tutor.core.context import UnifiedContext
from tutor.services.config.settings import Settings
from tutor.services.llm.base import LLMResponse


def _mock_llm(*responses: str):
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048
    queue = list(responses)

    async def call(req):
        c = queue.pop(0) if queue else "{}"
        return LLMResponse(content=c, model="mock", finish_reason="stop")

    llm.call = call
    return llm


@pytest.mark.asyncio
async def test_code_sandbox_drains_matplotlib_figures_to_artifacts():
    # XOR training snippet — the exact one the user reported broken.
    snippet = (
        "import numpy as np\n"
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "x = np.arange(10)\n"
        "y = x ** 2\n"
        "plt.plot(x, y)\n"
        "plt.title('loss curve')\n"
        "plt.show()\n"  # no-op under Agg; figure stays in memory
    )
    llm = _mock_llm(json.dumps({
        "title": "XOR 训练",
        "language": "python",
        "code": snippet,
        "explanation": "示例",
        "difficulty": 2,
    }, ensure_ascii=False))
    agent = CodeSandboxAgent(llm=llm)
    ctx = UnifiedContext(user_message="XOR")
    resource = await agent.process(ctx, topic="XOR")
    artifacts = resource.format_specific.get("artifacts") or []
    # At least one PNG artifact must have been produced by the drain.
    pngs = [a for a in artifacts if a.get("kind") == "png"]
    assert pngs, f"expected drained PNG artifacts, got: {artifacts!r}"
    # New writes expose a relocatable key, never an absolute host path.
    from tutor.services.artifacts import resolve_artifact_key
    from tutor.services.config.settings import get_settings

    for art in pngs:
        assert "path" not in art
        assert not art["artifact_key"].startswith(("/", "\\"))
        p = resolve_artifact_key(art["artifact_key"], get_settings().data_dir)
        assert p.exists(), f"artifact path missing: {p}"
        assert p.stat().st_size > 0, f"artifact is empty: {p}"


def test_ast_detects_matplotlib_imports_without_executing_code() -> None:
    assert _code_uses_matplotlib("import matplotlib as mpl")
    assert _code_uses_matplotlib("from matplotlib import pyplot as plt")
    assert _code_uses_matplotlib("import importlib\nimportlib.import_module('matplotlib.pyplot')")
    assert _code_uses_matplotlib("from importlib import import_module as load\nload('matplotlib.pyplot')")
    assert _code_uses_matplotlib("__import__('matplotlib.pyplot')")
    assert not _code_uses_matplotlib("import numpy as np\nprint(np.arange(2))")
    assert not _code_uses_matplotlib("import importlib\nimportlib.import_module('numpy')")
    # Invalid generated code remains a subprocess SyntaxError, not an AST crash.
    assert not _code_uses_matplotlib("def incomplete(")


def test_dynamic_matplotlib_import_captures_figure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    result = _run_plot(
        "from importlib import import_module as load\n"
        "plt = load('matplotlib.pyplot')\n"
        "plt.plot([1, 2])\n",
        settings=settings,
        monkeypatch=monkeypatch,
    )

    assert result[0] == "success"
    assert [artifact["name"] for artifact in result[5]] == ["figure_1.png"]


@pytest.mark.asyncio
async def test_figure_contract_without_artifact_is_typed_failure(tmp_path: Path) -> None:
    llm = _mock_llm(json.dumps({
        "title": "没有图像",
        "language": "python",
        "code": "print('no plot')",
        "explanation": "示例",
        "output_kind": "figure",
    }, ensure_ascii=False))
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)

    resource = await CodeSandboxAgent(llm=llm, settings=settings).process(
        UnifiedContext(), topic="figure"
    )

    assert resource.format_specific["output_kind"] == "figure"
    assert resource.format_specific["execution_status"] == "failed"
    assert resource.format_specific["error_code"] == "FIGURE_EXPECTED_BUT_NOT_PRODUCED"


def _run_plot(code: str, *, settings: Settings, monkeypatch):
    monkeypatch.setattr(
        "tutor.agents.resource.code_sandbox.get_settings",
        lambda: settings,
    )
    return _safe_run_python(
        code,
        interpreter=settings.execution_python or sys.executable,
        timeout=30,
        settings=settings,
    )


def test_show_captures_every_open_figure_before_user_closes_them(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    result = _run_plot(
        """
import matplotlib.pyplot as plt
plt.figure(); plt.plot([1, 2])
plt.figure(); plt.scatter([1], [2])
plt.show()
plt.close('all')
""",
        settings=settings,
        monkeypatch=monkeypatch,
    )

    status, _stdout, stderr, _error, _deps, artifacts, _duration = result
    assert status == "success"
    assert [artifact["name"] for artifact in artifacts] == [
        "figure_1.png",
        "figure_2.png",
    ]
    assert "FigureCanvasAgg is non-interactive" not in stderr


def test_final_drain_captures_figures_when_show_is_never_called(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    result = _run_plot(
        "import matplotlib.pyplot as plt\nplt.plot([1, 3, 2])",
        settings=settings,
        monkeypatch=monkeypatch,
    )

    assert result[0] == "success"
    assert [artifact["name"] for artifact in result[5]] == ["figure_1.png"]


def test_show_and_final_drain_do_not_duplicate_the_same_figure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    result = _run_plot(
        """
import matplotlib.pyplot as plt
plt.plot([1, 2])
plt.show()
plt.show()
""",
        settings=settings,
        monkeypatch=monkeypatch,
    )

    assert result[0] == "success"
    assert [artifact["name"] for artifact in result[5]] == ["figure_1.png"]


def test_figures_created_after_show_receive_the_next_artifact_number(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    result = _run_plot(
        """
import matplotlib.pyplot as plt
plt.figure(); plt.plot([1, 2])
plt.show()
plt.close('all')
plt.figure(); plt.plot([3, 4])
""",
        settings=settings,
        monkeypatch=monkeypatch,
    )

    assert result[0] == "success"
    assert [artifact["name"] for artifact in result[5]] == [
        "figure_1.png",
        "figure_2.png",
    ]


def test_matplotlib_capture_uses_required_export_quality() -> None:
    wrapped = _wrap_user_code("print('ok')", Path("scratch"), capture_matplotlib=True)
    assert "bbox_inches='tight'" in wrapped
    assert "dpi=160" in wrapped


def test_parent_matplotlib_environment_is_forced_before_user_first_line(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    monkeypatch.setenv("MPLBACKEND", "TkAgg")
    monkeypatch.setenv("MPLCONFIGDIR", str(tmp_path / "hostile-cache"))
    result = _run_plot(
        """
import os
assert os.environ['MPLBACKEND'] == 'Agg'
assert os.environ['MPLCONFIGDIR'].replace('\\\\', '/').endswith('/cache/matplotlib')
import matplotlib
assert matplotlib.get_backend().lower() == 'agg'
assert matplotlib.rcParams['axes.unicode_minus'] is False
""",
        settings=settings,
        monkeypatch=monkeypatch,
    )
    assert result[0] == "success", result[2]


def test_no_show_final_capture_failure_is_typed_and_cannot_report_success(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    result = _run_plot(
        """
import matplotlib.pyplot as plt
figure = plt.figure()
def fail_save(*args, **kwargs):
    raise PermissionError('private save path must not leak')
figure.savefig = fail_save
""",
        settings=settings,
        monkeypatch=monkeypatch,
    )

    assert result[0] == "failed"
    assert result[3] == "MATPLOTLIB_CAPTURE_FAILED"
    assert "[matplotlib capture failed]" in result[2]
    assert "private save path" not in result[2]


def test_user_exception_remains_primary_when_final_capture_also_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    result = _run_plot(
        """
import matplotlib.pyplot as plt
figure = plt.figure()
def fail_save(*args, **kwargs):
    raise PermissionError('secondary private capture failure')
figure.savefig = fail_save
raise ValueError('original user failure')
""",
        settings=settings,
        monkeypatch=monkeypatch,
    )

    assert result[0] == "failed"
    assert result[3] == "CODE_EXECUTION_FAILED"
    assert "original user failure" in result[2]
    assert "secondary private capture failure" not in result[2]
    assert "[matplotlib capture failed]" not in result[2]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))
