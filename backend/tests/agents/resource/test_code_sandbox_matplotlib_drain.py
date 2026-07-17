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
from tutor.agents.resource.code_sandbox import CodeSandboxAgent, _safe_run_python, _wrap_user_code
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
    wrapped = _wrap_user_code("print('ok')", Path("scratch"))
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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))
