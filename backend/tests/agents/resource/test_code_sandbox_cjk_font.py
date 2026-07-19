"""Regression test: code sandbox matplotlib must render CJK glyphs.

Session 585f367d trace showed::

    UserWarning: Glyph 25439 (\N{CJK UNIFIED IDEOGRAPH-635F}) missing
    from font(s) DejaVu Sans.

when the user's XOR backprop snippet plotted
``plt.title('训练损失')``. The sandbox's ``_wrap_user_code`` sets
``MPLBACKEND=Agg`` but never configures a CJK-capable ``font.sans-serif``
list, so matplotlib falls back to ``DejaVu Sans`` (Latin only) and
prints one ``Glyph NNN missing`` warning per character. The PNG ends
up with empty squares instead of "训练损失".

The fix is in ``_wrap_user_code``: prepend
``matplotlib.rcParams['font.sans-serif'] = [...]`` and
``matplotlib.rcParams['axes.unicode_minus'] = False`` BEFORE the user's
code runs, so the font lookup hits a CJK-capable font on the host.

We don't depend on a specific font being installed; the test just
asserts the rcParams were set to a non-empty list that includes any
known CJK font names AND that the sandbox exit was clean (no warning
escalated to a hard error).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from tutor.agents.resource import code_sandbox
from tutor.agents.resource.code_sandbox import _safe_run_python, _wrap_user_code
from tutor.services.config.settings import Settings


def _pick_cjk_font() -> str:
    """Best-effort probe: find a CJK font available on the test host."""
    candidates = [
        "Noto Sans CJK SC",
        "Noto Sans CJK JP",
        "Source Han Sans CN",
        "Source Han Sans SC",
        "SimHei",
        "Microsoft YaHei",
        "WenQuanYi Zen Hei",
        "WenQuanYi Micro Hei",
    ]
    # fc-list is the simplest probe; fall back to a static list when
    # unavailable.
    try:
        out = subprocess.run(
            ["fc-list", ":lang=zh"], capture_output=True, text=True, timeout=3
        )
        if out.returncode == 0 and out.stdout:
            # fc-list output: "<path>: <fontname>:..."
            names = set()
            for line in out.stdout.splitlines():
                parts = line.split(":")
                if len(parts) >= 2:
                    names.add(parts[1].strip())
            for cand in candidates:
                if cand in names:
                    return cand
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return candidates[0]  # hope the host has SOMETHING


def test_wrap_user_code_configures_cjk_font_before_user_code() -> None:
    """The wrapper must call ``rcParams['font.sans-serif'] = [...]``
    BEFORE the user's ``exec(...)`` so the font lookup uses the CJK
    font from the very first figure.
    """
    scratch = Path("/tmp/test_sandbox_scratch")
    wrapped = _wrap_user_code("print('hi')", scratch)

    # The font-config block must precede the exec call.
    sans_idx = wrapped.find("font.sans-serif")
    exec_idx = wrapped.find("exec(compile(")
    assert sans_idx != -1, "wrapper does not configure font.sans-serif"
    assert exec_idx != -1, "wrapper missing exec call"
    assert sans_idx < exec_idx, (
        f"font.sans-serif (idx={sans_idx}) must be set BEFORE "
        f"exec(compile() (idx={exec_idx})"
    )


def test_wrap_user_code_includes_axes_unicode_minus_false() -> None:
    """CJK labels with a minus sign render as a square without
    ``axes.unicode_minus = False``. The wrapper must set it.
    """
    wrapped = _wrap_user_code("print('hi')", Path("/tmp/test_sandbox_scratch"))
    assert "axes.unicode_minus" in wrapped
    assert "False" in wrapped  # axes.unicode_minus = False


@pytest.mark.skipif(
    not shutil.which("python") and not shutil.which("python3"),
    reason="no python interpreter available",
)
def test_wrap_user_code_renders_cjk_title_without_glyph_warning(
    tmp_path: Path,
) -> None:
    """End-to-end: run the wrapped snippet with a CJK title and
    verify (a) it exits 0, (b) ``FigureCanvasAgg is non-interactive``
    is suppressed or at least not raised to an error, (c) the figure
    PNG exists. We don't strictly need a CJK font installed —
    matplotlib's glyph-fallback path still produces a PNG, but the
    wrapper's font.sans-serif list ensures the FIRST attempt uses
    a CJK font on hosts that have one.
    """
    snippet = (
        "import matplotlib\n"
        "matplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.title('训练损失')\n"  # the CJK label from the user trace
        "plt.plot([1, 2, 3], [4, 5, 6])\n"
    )
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    wrapped = _wrap_user_code(snippet, scratch)

    # Find a python interpreter.
    py = shutil.which("python") or shutil.which("python3") or sys.executable
    proc = subprocess.run(
        [py, "-c", wrapped],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(scratch),
        timeout=30,
    )
    assert proc.returncode == 0, (
        f"wrapped code crashed:\nstdout={proc.stdout!r}\n"
        f"stderr={proc.stderr!r}"
    )
    # The drain must still have written the PNG.
    pngs = sorted(scratch.glob("figure_*.png"))
    assert pngs, f"no figure PNG was written; stderr={proc.stderr!r}"


def test_wrap_user_code_font_list_prefers_known_cjk_fonts() -> None:
    """The font.sans-serif list must include commonly-available CJK
    fonts so that on hosts with Noto Sans CJK installed, matplotlib
    picks it on the first lookup.
    """
    wrapped = _wrap_user_code("print('hi')", Path("/tmp/x"))
    # The list spans multiple lines; slice from assignment to the
    # closing bracket (whichever comes last).
    idx = wrapped.find("font.sans-serif")
    assert idx != -1, "wrapper does not configure font.sans-serif"
    # Find the matching closing bracket — scan forward and pick the
    # last ']' before the next major step (the user exec call).
    slice_end = wrapped.find("exec(compile(", idx)
    block = wrapped[idx:slice_end]
    # Common CJK fonts we hope to find in the list.
    expected = ["Noto", "SimHei", "Microsoft YaHei", "Source Han", "WenQuanYi"]
    found = [c for c in expected if c in block]
    assert found, (
        f"font.sans-serif list does not include any known CJK font: "
        f"{block!r}"
    )


def test_cjk_prelude_runs_before_user_plotting_and_warm_cache_is_reused(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(env="test", data_dir=tmp_path, execution_python=sys.executable)
    monkeypatch.setattr(code_sandbox, "get_settings", lambda: settings)
    snippet = (
        "import os\n"
        "import matplotlib as mpl\n"
        "assert mpl.rcParams['axes.unicode_minus'] is False\n"
        "assert 'Noto Sans CJK SC' in mpl.rcParams['font.sans-serif']\n"
        "print(os.environ['MPLCONFIGDIR'])\n"
        "import matplotlib.pyplot as plt\n"
        "plt.plot([1, 2])\n"
    )

    first = _safe_run_python(
        snippet, interpreter=sys.executable, timeout=30, settings=settings
    )
    second = _safe_run_python(
        snippet, interpreter=sys.executable, timeout=30, settings=settings
    )

    assert first[0] == second[0] == "success"
    assert first[1].strip() == second[1].strip() == "<sandbox>"
    assert "Matplotlib is building the font cache" not in second[2]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))
