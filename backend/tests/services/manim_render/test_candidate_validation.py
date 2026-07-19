from __future__ import annotations

from tutor.services.manim_render.candidate_validation import validate_manim_candidate

NAMESPACE = {
    "Animation",
    "BLUE",
    "Circle",
    "Create",
    "Dot",
    "FadeIn",
    "Line",
    "MainScene",
    "Scene",
    "Square",
    "Text",
    "VGroup",
    "Write",
}


def test_validator_rejects_bound_method_in_vgroup_and_zero_runtime(tmp_path):
    code = """from manim import *

class MainScene(Scene):
    def construct(self):
        line = Line()
        broken = VGroup(line.get_end)
        self.play(Create(line), run_time=0)
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=NAMESPACE,
    )

    assert {issue.code for issue in result.issues} >= {
        "BOUND_METHOD_IN_VGROUP",
        "NON_POSITIVE_RUN_TIME",
    }


def test_validator_rejects_unavailable_uppercase_manim_name(tmp_path):
    code = """from manim import *

class MainScene(Scene):
    def construct(self):
        self.add(ImaginaryMobject())
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=NAMESPACE,
    )

    assert "UNAVAILABLE_MANIM_SYMBOL" in {issue.code for issue in result.issues}


def test_validator_rejects_syntax_error(tmp_path):
    result = validate_manim_candidate(
        "class MainScene(Scene):\n    def construct(self)\n        pass\n",
        workdir=tmp_path,
        runtime_namespace=NAMESPACE,
    )

    assert "SYNTAX_ERROR" in {issue.code for issue in result.issues}


def test_validator_rejects_main_scene_without_scene_base(tmp_path):
    result = validate_manim_candidate(
        "class MainScene:\n    def construct(self):\n        pass\n",
        workdir=tmp_path,
        runtime_namespace=NAMESPACE,
    )

    assert "MISSING_MAIN_SCENE" in {issue.code for issue in result.issues}


def test_validator_rejects_missing_external_asset(tmp_path):
    code = """from manim import *

class MainScene(Scene):
    def construct(self):
        self.add(ImageMobject("invented.png"))
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace={*NAMESPACE, "ImageMobject"},
    )

    assert "MISSING_EXTERNAL_ASSET" in {issue.code for issue in result.issues}


def test_validator_accepts_complete_native_shape_scene(tmp_path):
    code = """from manim import *

class MainScene(Scene):
    def construct(self):
        circle = Circle(color=BLUE)
        square = Square()
        group = VGroup(circle, square)
        self.play(Create(circle), FadeIn(square), run_time=0.5)
        self.add(group)
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=NAMESPACE,
    )

    assert result.valid is True
    assert result.issues == ()
