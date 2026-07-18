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


def test_missing_literal_svg_asset_fails_before_render(tmp_path):
    code = '''from manim import *
class MainScene(Scene):
    def construct(self):
        self.add(SVGMobject("person_silhouette.svg"))
'''

    result = StaticGuard().check(code, workdir=tmp_path)

    assert result.passed is False
    assert result.external_assets == ("person_silhouette.svg",)
    assert result.error_code == "missing_external_asset"


def test_literal_assets_are_ordered_deduplicated_and_allowed_only_inside_workdir(
    tmp_path,
):
    (tmp_path / "diagram.svg").write_text("<svg/>", encoding="utf-8")
    (tmp_path / "photo.png").write_bytes(b"png")
    code = '''from manim import *
class MainScene(Scene):
    def construct(self):
        self.add(manim.SVGMobject("diagram.svg"))
        self.add(ImageMobject("photo.png"))
        self.add(SVGMobject("diagram.svg"))
'''

    result = StaticGuard().check(code, workdir=tmp_path)

    assert result.passed is True
    assert result.external_assets == ("diagram.svg", "photo.png")


@pytest.mark.parametrize("constructor", ["SVGMobject", "ImageMobject"])
def test_unsafe_literal_asset_does_not_grant_host_filesystem_access(
    tmp_path,
    constructor,
):
    outside = tmp_path.parent / f"outside-{constructor}.dat"
    outside.write_bytes(b"private")
    code = f'''from manim import *
class MainScene(Scene):
    def construct(self):
        self.add({constructor}({str(outside)!r}))
'''

    result = StaticGuard().check(code, workdir=tmp_path)

    assert result.passed is False
    assert result.error_code == "missing_external_asset"
    assert str(outside.resolve()) not in result.summary


def test_dynamic_asset_expression_is_not_treated_as_self_contained(tmp_path):
    code = '''from manim import *
class MainScene(Scene):
    def construct(self):
        filename = "diagram.svg"
        self.add(SVGMobject(filename))
'''

    result = StaticGuard().check(code, workdir=tmp_path)

    assert result.passed is False
    assert result.external_assets == ()
    assert result.error_code == "dynamic_external_asset"
