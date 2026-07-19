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


class _Mobject:
    def get_end(self):
        return None

    def get_left(self):
        return None

    def get_right(self):
        return None

    def replace(self, other):
        return other

    def rotate(self, angle):
        return angle

    def set_opacity(self, value):
        return value


class _Dot(_Mobject):
    pass


class _Axes(_Mobject):
    x_axis = object()


class _Scene:
    pass


RUNTIME_NAMESPACE = {
    **{name: object() for name in NAMESPACE},
    "Mobject": _Mobject,
    "Dot": _Dot,
    "Axes": _Axes,
    "Scene": _Scene,
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
        runtime_namespace=RUNTIME_NAMESPACE,
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
        runtime_namespace=RUNTIME_NAMESPACE,
    )

    assert "UNAVAILABLE_MANIM_SYMBOL" in {issue.code for issue in result.issues}


def test_validator_rejects_syntax_error(tmp_path):
    result = validate_manim_candidate(
        "class MainScene(Scene):\n    def construct(self)\n        pass\n",
        workdir=tmp_path,
        runtime_namespace=RUNTIME_NAMESPACE,
    )

    assert "SYNTAX_ERROR" in {issue.code for issue in result.issues}


def test_validator_rejects_main_scene_without_scene_base(tmp_path):
    result = validate_manim_candidate(
        "class MainScene:\n    def construct(self):\n        pass\n",
        workdir=tmp_path,
        runtime_namespace=RUNTIME_NAMESPACE,
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
        runtime_namespace={**RUNTIME_NAMESPACE, "ImageMobject": object()},
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
        runtime_namespace=RUNTIME_NAMESPACE,
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
        runtime_namespace=RUNTIME_NAMESPACE,
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
        runtime_namespace=RUNTIME_NAMESPACE,
    )

    assert {issue.code for issue in result.issues} & {
        "EXTERNAL_IO",
        "DYNAMIC_IMPORT",
        "DISALLOWED_NUMPY_CALL",
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
        runtime_namespace=RUNTIME_NAMESPACE,
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
        runtime_namespace=RUNTIME_NAMESPACE,
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
        runtime_namespace=RUNTIME_NAMESPACE,
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
        runtime_namespace=RUNTIME_NAMESPACE,
    )

    assert "UNAVAILABLE_MANIM_SYMBOL" in {issue.code for issue in result.issues}


@pytest.mark.parametrize(
    "statement",
    [
        "getattr(__builtins__, 'open')('secret.txt')",
        "__builtins__['open']('secret.txt')",
        "().__class__.__mro__[1].__subclasses__()",
        "globals()",
        "vars(self)",
        "compile('1', '<x>', 'eval')",
    ],
)
def test_validator_rejects_builtin_and_object_model_sandbox_escapes(
    tmp_path,
    statement,
):
    code = f"""from manim import *
class MainScene(Scene):
    def construct(self):
        {statement}
        self.add(Dot())
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=RUNTIME_NAMESPACE,
    )

    assert "UNSAFE_PYTHON_SURFACE" in {issue.code for issue in result.issues}


def test_manim_module_alias_validates_runtime_symbols(tmp_path):
    valid = """import manim as m
class MainScene(m.Scene):
    def construct(self):
        self.add(m.Dot())
"""
    missing = valid.replace("m.Dot()", "m.MissingMobject()")

    valid_result = validate_manim_candidate(
        valid,
        workdir=tmp_path,
        runtime_namespace=RUNTIME_NAMESPACE,
    )
    missing_result = validate_manim_candidate(
        missing,
        workdir=tmp_path,
        runtime_namespace=RUNTIME_NAMESPACE,
    )

    assert valid_result.valid is True
    assert "UNAVAILABLE_MANIM_SYMBOL" in {
        issue.code for issue in missing_result.issues
    }


def test_non_manim_module_alias_is_not_checked_as_manim_namespace(tmp_path):
    code = """import math as m
from manim import Scene, Dot
class MainScene(Scene):
    def construct(self):
        self.add(Dot().shift([m.sin(0.0), 0, 0]))
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=RUNTIME_NAMESPACE,
    )

    assert result.valid is True


@pytest.mark.parametrize(
    "method",
    ["get_left", "get_right", "set_opacity", "replace"],
)
def test_vgroup_rejects_runtime_derived_mobject_bound_methods(tmp_path, method):
    code = f"""from manim import *
class MainScene(Scene):
    def construct(self):
        dot = Dot()
        self.add(VGroup(dot.{method}))
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=RUNTIME_NAMESPACE,
    )

    assert "BOUND_METHOD_IN_VGROUP" in {issue.code for issue in result.issues}


def test_repair_candidate_rejects_existing_external_asset(tmp_path):
    (tmp_path / "existing.svg").write_text("<svg/>", encoding="utf-8")
    code = """from manim import *
class MainScene(Scene):
    def construct(self):
        self.add(SVGMobject("existing.svg"))
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace={**RUNTIME_NAMESPACE, "SVGMobject": object()},
    )

    assert "EXTERNAL_ASSET" in {issue.code for issue in result.issues}


@pytest.mark.parametrize(
    ("filename", "statement", "runtime_symbol"),
    [
        ("existing.png", 'self.add(ImageMobject("existing.png"))', "ImageMobject"),
        ("existing.wav", 'self.add_sound("existing.wav")', None),
    ],
)
def test_repair_candidate_rejects_all_existing_asset_kinds(
    tmp_path,
    filename,
    statement,
    runtime_symbol,
):
    (tmp_path / filename).write_bytes(b"asset")
    namespace = dict(RUNTIME_NAMESPACE)
    if runtime_symbol:
        namespace[runtime_symbol] = object()
    code = f"""from manim import *
class MainScene(Scene):
    def construct(self):
        {statement}
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=namespace,
    )

    assert "EXTERNAL_ASSET" in {issue.code for issue in result.issues}


def test_validator_allows_string_and_mobject_replace_calls(tmp_path):
    code = """from manim import *
class MainScene(Scene):
    def construct(self):
        label = "before".replace("before", "after")
        dot = Dot()
        dot.replace(Dot())
        self.add(dot)
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=RUNTIME_NAMESPACE,
    )

    assert result.valid is True


@pytest.mark.parametrize(
    "statement",
    [
        "np.lib.format.open_memmap('escape.npy')",
        "np.array([1.0]).dump('escape.pkl')",
    ],
)
def test_validator_rejects_numpy_namespace_and_ndarray_file_escapes(
    tmp_path,
    statement,
):
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
        runtime_namespace=RUNTIME_NAMESPACE,
    )

    assert {issue.code for issue in result.issues} & {
        "DISALLOWED_NUMPY_CALL",
        "EXTERNAL_IO",
    }


def test_validator_allows_explicit_numpy_computation_surface(tmp_path):
    code = """from manim import *
import numpy as np
class MainScene(Scene):
    def construct(self):
        x = np.arange(3)
        y = np.linspace(0.0, 1.0, 3)
        base = np.array([x, y, np.zeros(3), np.ones(3)])
        points = np.stack([base[0], np.sin(base[1]), np.exp(base[1])]).reshape((-1, 3))
        jitter = np.random.normal(0.0, 0.1, 3) + np.random.uniform(0.0, 0.1, 3)
        self.add(Dot(point=points[0] + jitter))
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace=RUNTIME_NAMESPACE,
    )

    assert result.valid is True


@pytest.mark.parametrize(
    "statement",
    [
        "writer = np.save\n        writer('escape.npy', [1])",
        "writer = np.array([1]).tofile\n        writer('escape.bin')",
        "writer = np.lib.format.open_memmap\n        writer('escape.npy')",
        "writer = Dot().tofile",
    ],
)
def test_validator_rejects_numpy_and_file_method_attribute_aliases(
    tmp_path,
    statement,
):
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
        runtime_namespace=RUNTIME_NAMESPACE,
    )

    assert {issue.code for issue in result.issues} & {
        "DISALLOWED_NUMPY_ATTRIBUTE",
        "EXTERNAL_IO",
    }


@pytest.mark.parametrize(
    ("assignment", "call"),
    [
        ("maker = alias = SVGMobject", "self.add(alias('x.svg'))"),
        (
            "maker = manim.ImageMobject\n        alias = maker",
            "self.add(alias('x.png'))",
        ),
        (
            "maker = self.add_sound\n        alias = maker",
            "alias('x.wav')",
        ),
    ],
)
def test_validator_rejects_asset_constructor_assignment_aliases(
    tmp_path,
    assignment,
    call,
):
    code = f"""import manim
from manim import *
class MainScene(Scene):
    def construct(self):
        {assignment}
        {call}
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace={
            **RUNTIME_NAMESPACE,
            "SVGMobject": object(),
            "ImageMobject": object(),
        },
    )

    assert "EXTERNAL_ASSET" in {issue.code for issue in result.issues}


@pytest.mark.parametrize(
    "statement",
    [
        "maker = [SVGMobject][0]",
        "(maker,) = (ImageMobject,)",
        "maker = lambda: SVGMobject",
        "makers = [manim.ImageMobject]",
        "sounds = (self.add_sound,)",
    ],
)
def test_validator_rejects_asset_constructor_expression_references(
    tmp_path,
    statement,
):
    code = f"""import manim
from manim import *
class MainScene(Scene):
    def construct(self):
        {statement}
        self.add(Dot())
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace={
            **RUNTIME_NAMESPACE,
            "SVGMobject": object(),
            "ImageMobject": object(),
        },
    )

    assert "EXTERNAL_ASSET" in {issue.code for issue in result.issues}


def test_validator_rejects_non_mapping_runtime_namespace_fail_closed(tmp_path):
    code = """from manim import *
class MainScene(Scene):
    def construct(self):
        dot = Dot()
        self.add(VGroup(dot.get_left))
"""

    result = validate_manim_candidate(
        code,
        workdir=tmp_path,
        runtime_namespace={"Scene", "Dot", "VGroup", "Mobject"},  # type: ignore[arg-type]
    )

    assert "INVALID_RUNTIME_NAMESPACE" in {issue.code for issue in result.issues}
