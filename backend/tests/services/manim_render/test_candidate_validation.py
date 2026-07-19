from __future__ import annotations

import pytest
from tutor.services.manim_render.candidate_validation import validate_manim_candidate

NAMESPACE = {
    "Animation",
    "Axes",
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


@pytest.mark.parametrize(
    "statement",
    [
        "import requests",
        "import httpx",
        "import socket",
        "import os",
        "import pathlib",
        "import shutil",
        "from urllib.request import urlopen",
    ],
)
def test_validator_rejects_non_allowlisted_imports(tmp_path, statement):
    code = f"""{statement}
from manim import *
class MainScene(Scene):
    def construct(self):
        self.add(Dot())
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=NAMESPACE,
    )

    assert "DISALLOWED_IMPORT" in {issue.code for issue in result.issues}


@pytest.mark.parametrize(
    "statement",
    [
        "open('secret.txt').read()",
        "__import__('os').environ.get('SECRET')",
        "getattr(__builtins__, '__import__')('socket')",
        "Path('secret.txt').read_text()",
        "os.environ['SECRET']",
        "requests.get('https://example.com')",
        "urllib.request.urlopen('https://example.com')",
        "socket.create_connection(('example.com', 443))",
        "np.load('secret.npy')",
        "numpy.save('secret.npy', [1])",
        "np.genfromtxt('secret.csv')",
        "np.memmap('secret.bin')",
    ],
)
def test_validator_rejects_external_io_bypasses(tmp_path, statement):
    code = f"""from manim import *
import numpy as np
class MainScene(Scene):
    def construct(self):
        {statement}
        self.add(Dot())
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=NAMESPACE,
    )

    assert {issue.code for issue in result.issues} & {
        "EXTERNAL_IO",
        "DYNAMIC_IMPORT",
    }


def test_validator_allows_math_numpy_computation_and_native_manim(tmp_path):
    code = """from manim import Scene, Dot
import numpy as np
from math import sin
class MainScene(Scene):
    def construct(self):
        values = np.array([sin(0.0), 1.0])
        self.add(Dot(point=[values[0], values[1], 0]))
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace={*NAMESPACE, "Scene", "Dot"},
    )

    assert result.valid is True


def test_validator_allows_mobject_attribute_in_vgroup(tmp_path):
    code = """from manim import *
class MainScene(Scene):
    def construct(self):
        axes = Axes()
        group = VGroup(axes.x_axis)
        self.add(group)
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=NAMESPACE,
    )

    assert "BOUND_METHOD_IN_VGROUP" not in {issue.code for issue in result.issues}
    assert result.valid is True


def test_validator_rejects_known_bound_mutator_in_vgroup(tmp_path):
    code = """from manim import *
class MainScene(Scene):
    def construct(self):
        dot = Dot()
        self.add(VGroup(dot.rotate))
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=NAMESPACE,
    )

    assert "BOUND_METHOD_IN_VGROUP" in {issue.code for issue in result.issues}


def test_explicit_missing_manim_import_is_checked_against_runtime(tmp_path):
    code = """from manim import Scene, MissingName
class MainScene(Scene):
    def construct(self):
        self.add(MissingName())
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=NAMESPACE,
    )

    assert "UNAVAILABLE_MANIM_SYMBOL" in {issue.code for issue in result.issues}
