"""Test the general-Python fallback in _extract_first_python_block.

The pre-fix helper only recognized Manim-specific code. CodeSandbox
snippets like ``import numpy as np`` or ``def sigmoid(z):`` returned
``""`` and the user saw "代码生成失败" even though the LLM had written
perfectly valid code without code fences.
"""

from __future__ import annotations

import sys

import pytest

from tutor.agents.resource.manim_video import _extract_first_python_block


def test_extract_general_python_import():
    raw = (
        '{"title": "sigmoid", "code": "'
        'import numpy as np\n'
        'print(np.exp(0))\n'
        '"}'
    )
    out = _extract_first_python_block(raw)
    assert "import numpy as np" in out
    assert "print(np.exp(0))" in out
    # Trailing JSON tail must be gone.
    assert not out.endswith('"')


def test_extract_general_python_def():
    raw = (
        "Here is your code:\n"
        "def sigmoid(z):\n"
        "    return 1 / (1 + 2 ** (-z))\n"
        "\n"
        "print(sigmoid(0))\n"
    )
    out = _extract_first_python_block(raw)
    assert out.startswith("def sigmoid")
    assert "1 / (1 + 2 ** (-z))" in out


def test_extract_general_python_class():
    raw = (
        "class Sigmoid:\n"
        "    def __call__(self, z):\n"
        "        return 1 / (1 + 2 ** -z)\n"
    )
    out = _extract_first_python_block(raw)
    assert out.startswith("class Sigmoid")


def test_extract_manim_still_works():
    """Make sure the Manim path is preserved (don't regress)."""
    raw = (
        "from manim import *\n\n"
        "class MainScene(Scene):\n"
        "    def construct(self):\n"
        "        self.play(Write(Text('hi')))\n"
    )
    out = _extract_first_python_block(raw)
    assert out.startswith("from manim")
    assert "class MainScene" in out


def test_extract_fenced_still_works():
    raw = (
        "Some prose.\n"
        "```python\n"
        "def hello():\n"
        "    print('hi')\n"
        "```\n"
        "More prose.\n"
    )
    out = _extract_first_python_block(raw)
    assert out == "def hello():\n    print('hi')"


def test_extract_empty_input():
    assert _extract_first_python_block("") == ""
    assert _extract_first_python_block(None) == ""  # type: ignore[arg-type]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))  # noqa: F821