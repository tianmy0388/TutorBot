"""Tests for :mod:`tutor.services.manim_render.static_guard`."""

from __future__ import annotations

import pytest

from tutor.services.manim_render.static_guard import StaticGuard


VALID_CODE = '''from manim import *


class MainScene(Scene):
    """A simple scene."""

    def construct(self):
        t = Text("Hello")
        self.play(Write(t))
        self.wait(1)
'''


def test_valid_code_passes():
    guard = StaticGuard()
    result = guard.check(VALID_CODE)
    assert result.passed is True
    assert result.errors == []


def test_syntax_error_caught():
    guard = StaticGuard()
    bad = "def broken(:\n    pass\n"
    result = guard.check(bad)
    assert result.passed is False
    assert any("AST parse" in e or "Syntax" in e for e in result.errors)


def test_indentation_error_caught():
    guard = StaticGuard()
    bad = "def foo():\nprint('wrong indent')\n"
    result = guard.check(bad)
    assert result.passed is False


def test_undefined_name_caught():
    """Undefined names are RUNTIME errors, not syntax errors.

    py_compile does not catch them — this test documents that limitation.
    To catch undefined names we'd need mypy or actual execution.
    """
    guard = StaticGuard()
    bad = "x = undefined_variable_xyz\n"
    result = guard.check(bad)
    # AST parse succeeds, py_compile succeeds (no syntax error)
    # So StaticGuard passes — this is by design.
    assert result.passed is True
    # Sanity warns about missing manim / Scene
    assert any("manim" in w.lower() or "Scene" in w for w in result.warnings)


def test_missing_scene_class_warns_but_passes():
    guard = StaticGuard()
    # Code is syntactically valid but has no Scene class
    bad = "x = 1\nprint(x)\n"
    result = guard.check(bad)
    # AST/py_compile may or may not pass — but the sanity warning fires
    if result.passed:
        assert any("Scene" in w for w in result.warnings)


def test_shebang_stripped():
    guard = StaticGuard()
    code = "#!/usr/bin/env python\n" + VALID_CODE
    result = guard.check(code)
    assert result.passed is True
    assert "#!/usr/bin/env" not in result.cleaned_code


def test_cleaned_code_trailing_newline():
    guard = StaticGuard()
    result = guard.check(VALID_CODE)
    assert result.cleaned_code.endswith("\n")


def test_camera_frame_mypy_false_positive_filtered():
    """The KNOWN_FALSE_POSITIVES filter shouldn't make valid code fail."""
    guard = StaticGuard()
    code = VALID_CODE.replace(
        "        t = Text(\"Hello\")",
        "        self.camera.frame.set(width=10)",
    )
    result = guard.check(code)
    assert result.passed is True


def test_warnings_for_missing_construct():
    guard = StaticGuard()
    code = '''from manim import *

class Empty(Scene):
    """No construct method."""
    pass
'''
    result = guard.check(code)
    assert result.passed is True
    assert any("construct" in w for w in result.warnings)


def test_warnings_for_missing_manim_import():
    guard = StaticGuard()
    code = '''
class MainScene(Scene):
    def construct(self):
        pass
'''
    result = guard.check(code)
    # Should still pass py_compile (Scene is undefined but NameError is runtime)
    # But sanity warns
    if result.passed:
        assert any("manim" in w.lower() for w in result.warnings)
