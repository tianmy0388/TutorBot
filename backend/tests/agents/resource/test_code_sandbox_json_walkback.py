"""Regression test for single-line JSON-wrapped code salvage.

Pre-fix, ``_extract_first_python_block`` walked back from the matched
``import``/``def``/``class`` to the previous ``\\n`` (real newline).
When the LLM returned a single-line JSON like::

    {"title": "...", "code": "import math\\n\\ndef sigmoid(z):\\n  ..."}

…there is **no real newline** in the input — the ``\\n`` chars are
JSON-escaped sequences inside the string value. The walkback fell
through to position 0 and the recovered code started with the JSON
wrapper::

    { "title": "...", "code": "import math
    def sigmoid(z):
        ...

…and ran with a SyntaxError on the ``{`` prefix.

The fix walks back to the nearest JSON-string boundary (``"`` quote)
when no newline precedes the code, so the recovered snippet begins at
the first character of the actual code.
"""

from __future__ import annotations

import sys

import pytest

from tutor.agents.resource.manim_video import _extract_first_python_block


def test_extract_single_line_json_with_import():
    """LLM returns the whole payload on one line; ``\\n`` is JSON-escaped."""
    raw = (
        '{ "title": "x", "language": "python", "code": '
        '"import math\\n\\n'
        'def sigmoid(z):\\n'
        '    return 1 / (1 + math.exp(-z))\\n\\n'
        'print(sigmoid(0))\\n"'
        '}'
    )
    out = _extract_first_python_block(raw)
    # The recovered code MUST start with ``import math``, not with
    # the JSON wrapper.
    assert out.startswith("import math"), f"got: {out[:80]!r}"
    # Must NOT contain the JSON key/value wrapper.
    assert '"code":' not in out
    assert '"title":' not in out
    # The body should include the def and the print.
    assert "def sigmoid" in out
    assert "print(sigmoid(0))" in out


def test_extract_single_line_json_with_def():
    """LLM puts ``def`` as the first code construct inside JSON string."""
    raw = (
        '{ "code": '
        '"def hello():\\n'
        '    print(\'hi\')\\n"'
        '}'
    )
    out = _extract_first_python_block(raw)
    assert out.startswith("def hello"), f"got: {out[:60]!r}"
    assert '"code":' not in out
    assert "print('hi')" in out


def test_extract_single_line_json_with_class():
    raw = (
        '{ "code": '
        '"class Sigmoid:\\n'
        '    def __call__(self, z):\\n'
        '        return 1 / (1 + 2 ** -z)\\n"'
        '}'
    )
    out = _extract_first_python_block(raw)
    assert out.startswith("class Sigmoid"), f"got: {out[:60]!r}"
    assert '"code":' not in out
    assert "def __call__" in out


def test_extract_truncated_json_still_recovers_cleanly():
    """When the LLM hit ``max_tokens`` mid-string, the JSON is unterminated
    but the code inside the JSON string is still recoverable."""
    raw = (
        '{ "title": "x", "language": "python", "code": '
        '"import math\\n\\n'
        'def sigmoid(z):\\n'
        '    return 1 / (1 + math.exp(-z))\\n\\n'
        'print(sigmoid(0))\\n"'
        # ↑ No closing ``}`` — truncated by max_tokens.
    )
    out = _extract_first_python_block(raw)
    assert out.startswith("import math"), f"got: {out[:80]!r}"
    assert '"code":' not in out
    assert "def sigmoid" in out


def test_extract_multiline_still_works():
    """Don't regress the well-formed multi-line case."""
    raw = (
        '{ "code": "\n'
        'import numpy as np\n'
        'print(np.exp(0))\n'
        '"\n'
        '}'
    )
    out = _extract_first_python_block(raw)
    assert out.startswith("import numpy"), f"got: {out[:60]!r}"
    assert '"code":' not in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))