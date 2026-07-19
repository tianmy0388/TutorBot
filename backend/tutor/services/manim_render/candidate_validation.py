"""Deterministic validation for complete LLM-generated Manim candidates."""

from __future__ import annotations

import ast
import inspect
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from tutor.services.manim_render.static_guard import StaticGuard

_ALLOWED_IMPORT_ROOTS = {"manim", "math", "numpy"}
_DANGEROUS_NAMES = {
    "__builtins__",
    "__import__",
    "breakpoint",
    "compile",
    "delattr",
    "dir",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "memoryview",
    "object",
    "open",
    "setattr",
    "type",
    "vars",
}
_SAFE_BUILTINS = {
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "filter",
    "float",
    "int",
    "isinstance",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "range",
    "reversed",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "super",
    "tuple",
    "zip",
}
_FORBIDDEN_IO_ROOTS = {
    "httpx",
    "os",
    "pathlib",
    "requests",
    "shutil",
    "socket",
    "subprocess",
    "urllib",
}
_FORBIDDEN_FILE_METHODS = {
    "glob",
    "iterdir",
    "mkdir",
    "open",
    "read_bytes",
    "read_text",
    "rename",
    "rglob",
    "rmdir",
    "tofile",
    "touch",
    "unlink",
    "write_bytes",
    "write_text",
}
_FORBIDDEN_NUMPY_IO = {
    "fromfile",
    "genfromtxt",
    "load",
    "loadtxt",
    "memmap",
    "save",
    "savetxt",
    "savez",
    "savez_compressed",
}


@dataclass(frozen=True)
class CandidateValidationIssue:
    code: str
    message: str
    line: int | None = None


@dataclass(frozen=True)
class CandidateValidation:
    valid: bool
    issues: tuple[CandidateValidationIssue, ...] = ()


def validate_manim_candidate(
    code: str,
    *,
    workdir: Path,
    runtime_namespace: Mapping[str, object] | Collection[str],
) -> CandidateValidation:
    """Reject deterministic source defects before spending a render attempt."""
    issues: list[CandidateValidationIssue] = []
    try:
        tree = ast.parse(code)
        compile(code, "<manim-candidate>", "exec")
    except (SyntaxError, ValueError, TypeError) as exc:
        return CandidateValidation(
            valid=False,
            issues=(
                CandidateValidationIssue(
                    code="SYNTAX_ERROR",
                    message=str(exc)[:500],
                    line=getattr(exc, "lineno", None),
                ),
            ),
        )

    guard = StaticGuard().check(code, workdir=workdir)
    if not guard.passed:
        guard_code = {
            "syntax_error": "SYNTAX_ERROR",
            "compile_error": "COMPILE_ERROR",
            "missing_external_asset": "MISSING_EXTERNAL_ASSET",
            "dynamic_external_asset": "MISSING_EXTERNAL_ASSET",
        }.get(guard.error_code, "STATIC_GUARD_REJECTED")
        issues.append(
            CandidateValidationIssue(
                code=guard_code,
                message=(guard.summary or "; ".join(guard.errors))[:500],
            )
        )
    if guard.external_assets or guard.error_code in {
        "dynamic_external_asset",
        "missing_external_asset",
    }:
        issues.append(
            CandidateValidationIssue(
                code="EXTERNAL_ASSET",
                message="Repair candidates must be self-contained without external assets",
            )
        )

    main_scene = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MainScene"
        ),
        None,
    )
    has_scene_base = bool(
        main_scene
        and any(
            _call_name(base).rsplit(".", 1)[-1] == "Scene"
            for base in main_scene.bases
        )
    )
    has_construct = bool(
        main_scene
        and any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "construct"
            for node in main_scene.body
        )
    )
    if main_scene is None or not has_scene_base or not has_construct:
        issues.append(
            CandidateValidationIssue(
                code="MISSING_MAIN_SCENE",
                message="Candidate must define MainScene.construct",
                line=getattr(main_scene, "lineno", None),
            )
        )

    runtime_names = set(runtime_namespace)
    aliases = _import_aliases(tree)
    issues.extend(_validate_imports(tree, runtime_names))
    issues.extend(_validate_manim_aliases(tree, aliases, runtime_names))
    issues.extend(_validate_external_io(tree, aliases))
    locally_defined = _defined_names(tree)
    issues.extend(
        _validate_python_surface(tree, runtime_names, locally_defined)
    )
    bound_mobject_methods = _runtime_mobject_methods(runtime_namespace)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node.func)
        if call_name.rsplit(".", 1)[-1] == "VGroup":
            for argument in node.args:
                if (
                    isinstance(argument, ast.Attribute)
                    and argument.attr in bound_mobject_methods
                ):
                    issues.append(
                        CandidateValidationIssue(
                            code="BOUND_METHOD_IN_VGROUP",
                            message="VGroup arguments must be Mobjects, not bound methods",
                            line=argument.lineno,
                        )
                    )
        for keyword in node.keywords:
            if keyword.arg != "run_time":
                continue
            value = _numeric_constant(keyword.value)
            if value is not None and value <= 0:
                issues.append(
                    CandidateValidationIssue(
                        code="NON_POSITIVE_RUN_TIME",
                        message="run_time must be greater than zero",
                        line=keyword.value.lineno,
                    )
                )

    for node in ast.walk(tree):
        if not isinstance(node, ast.Name) or not isinstance(node.ctx, ast.Load):
            continue
        if not node.id[:1].isupper():
            continue
        if node.id in runtime_names or node.id in locally_defined:
            continue
        issues.append(
            CandidateValidationIssue(
                code="UNAVAILABLE_MANIM_SYMBOL",
                message=f"{node.id} is not available in this Manim runtime",
                line=node.lineno,
            )
        )

    unique: list[CandidateValidationIssue] = []
    seen: set[tuple[str, int | None, str]] = set()
    for issue in issues:
        key = (issue.code, issue.line, issue.message)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return CandidateValidation(valid=not unique, issues=tuple(unique))


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _numeric_constant(node: ast.AST) -> float | None:
    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        return float(node.value)
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, (ast.USub, ast.UAdd))
        and isinstance(node.operand, ast.Constant)
        and type(node.operand.value) in {int, float}
    ):
        value = float(node.operand.value)
        return -value if isinstance(node.op, ast.USub) else value
    return None


def _defined_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
        elif (
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".", 1)[0] != "manim"
        ):
            names.update(
                alias.asname or alias.name
                for alias in node.names
                if alias.name != "*"
            )
    return names


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".", 1)[0]
                aliases[local] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                if alias.name != "*":
                    aliases[alias.asname or alias.name] = (
                        f"{node.module}.{alias.name}"
                    )
    return aliases


def _validate_imports(
    tree: ast.AST,
    runtime_names: set[str],
) -> list[CandidateValidationIssue]:
    issues: list[CandidateValidationIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root not in _ALLOWED_IMPORT_ROOTS:
                    issues.append(
                        CandidateValidationIssue(
                            code="DISALLOWED_IMPORT",
                            message=f"Import of {root} is not allowed in Manim repair",
                            line=node.lineno,
                        )
                    )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".", 1)[0]
            if root not in _ALLOWED_IMPORT_ROOTS:
                issues.append(
                    CandidateValidationIssue(
                        code="DISALLOWED_IMPORT",
                        message=f"Import from {root or '<relative>'} is not allowed",
                        line=node.lineno,
                    )
                )
                continue
            if any(alias.name == "*" for alias in node.names) and root != "manim":
                issues.append(
                    CandidateValidationIssue(
                        code="DISALLOWED_IMPORT",
                        message=f"Wildcard import from {root} is not allowed",
                        line=node.lineno,
                    )
                )
            if root == "manim":
                for alias in node.names:
                    if alias.name != "*" and alias.name not in runtime_names:
                        issues.append(
                            CandidateValidationIssue(
                                code="UNAVAILABLE_MANIM_SYMBOL",
                                message=(
                                    f"{alias.name} is not available in this "
                                    "Manim runtime"
                                ),
                                line=node.lineno,
                            )
                        )
            if root == "numpy":
                for alias in node.names:
                    if alias.name in _FORBIDDEN_NUMPY_IO:
                        issues.append(
                            CandidateValidationIssue(
                                code="EXTERNAL_IO",
                                message=f"numpy.{alias.name} file I/O is not allowed",
                                line=node.lineno,
                            )
                        )
    return issues


def _validate_manim_aliases(
    tree: ast.AST,
    aliases: dict[str, str],
    runtime_names: set[str],
) -> list[CandidateValidationIssue]:
    issues: list[CandidateValidationIssue] = []
    manim_aliases = {
        alias for alias, target in aliases.items() if target == "manim"
    }
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Attribute)
            or not isinstance(node.value, ast.Name)
            or node.value.id not in manim_aliases
            or node.attr in runtime_names
        ):
            continue
        issues.append(
            CandidateValidationIssue(
                code="UNAVAILABLE_MANIM_SYMBOL",
                message=f"{node.attr} is not available in this Manim runtime",
                line=node.lineno,
            )
        )
    return issues


def _validate_python_surface(
    tree: ast.AST,
    runtime_names: set[str],
    locally_defined: set[str],
) -> list[CandidateValidationIssue]:
    """Allow only explicit runtime/local names and a small pure-builtin set."""
    issues: list[CandidateValidationIssue] = []
    allowed_names = runtime_names | locally_defined | _SAFE_BUILTINS
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and _is_dunder(node.attr):
            issues.append(
                CandidateValidationIssue(
                    code="UNSAFE_PYTHON_SURFACE",
                    message="Python dunder object-model access is not allowed",
                    line=node.lineno,
                )
            )
        elif (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and (
                node.id in _DANGEROUS_NAMES
                or _is_dunder(node.id)
                or node.id not in allowed_names
            )
        ):
            issues.append(
                CandidateValidationIssue(
                    code="UNSAFE_PYTHON_SURFACE",
                    message=f"Python name {node.id!r} is not allowed in repair code",
                    line=node.lineno,
                )
            )
    return issues


def _is_dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def _runtime_mobject_methods(
    runtime_namespace: Mapping[str, object] | Collection[str],
) -> set[str]:
    if not isinstance(runtime_namespace, Mapping):
        return set()
    mobject = runtime_namespace.get("Mobject")
    if not inspect.isclass(mobject):
        return set()
    methods: set[str] = set()
    for value in runtime_namespace.values():
        if not inspect.isclass(value):
            continue
        try:
            if not issubclass(value, mobject):
                continue
        except TypeError:
            continue
        methods.update(
            name
            for name in dir(value)
            if not _is_dunder(name) and callable(getattr(value, name, None))
        )
    return methods


def _validate_external_io(
    tree: ast.AST,
    aliases: dict[str, str],
) -> list[CandidateValidationIssue]:
    issues: list[CandidateValidationIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if _is_dynamic_import_call(node):
                issues.append(
                    CandidateValidationIssue(
                        code="DYNAMIC_IMPORT",
                        message="Dynamic imports are not allowed",
                        line=node.lineno,
                    )
                )
                continue
            raw_name = _call_name(node.func)
            name = _resolve_alias(raw_name, aliases)
            root = name.split(".", 1)[0]
            terminal = name.rsplit(".", 1)[-1]
            if raw_name == "open" or raw_name == "Path":
                issues.append(
                    CandidateValidationIssue(
                        code="EXTERNAL_IO",
                        message=f"{raw_name} filesystem access is not allowed",
                        line=node.lineno,
                    )
                )
            elif root in _FORBIDDEN_IO_ROOTS:
                issues.append(
                    CandidateValidationIssue(
                        code="EXTERNAL_IO",
                        message=f"External I/O call {name} is not allowed",
                        line=node.lineno,
                    )
                )
            elif root == "numpy" and terminal in _FORBIDDEN_NUMPY_IO:
                issues.append(
                    CandidateValidationIssue(
                        code="EXTERNAL_IO",
                        message=f"numpy.{terminal} file I/O is not allowed",
                        line=node.lineno,
                    )
                )
            elif terminal in _FORBIDDEN_FILE_METHODS:
                issues.append(
                    CandidateValidationIssue(
                        code="EXTERNAL_IO",
                        message=f"Filesystem method {terminal} is not allowed",
                        line=node.lineno,
                    )
                )
        elif isinstance(node, ast.Attribute):
            name = _resolve_alias(_call_name(node), aliases)
            root = name.split(".", 1)[0]
            if root in _FORBIDDEN_IO_ROOTS:
                issues.append(
                    CandidateValidationIssue(
                        code="EXTERNAL_IO",
                        message=f"External I/O namespace {root} is not allowed",
                        line=node.lineno,
                    )
                )
    return issues


def _resolve_alias(name: str, aliases: dict[str, str]) -> str:
    if not name:
        return ""
    root, separator, remainder = name.partition(".")
    target = aliases.get(root, root)
    return f"{target}.{remainder}" if separator else target


def _is_dynamic_import_call(node: ast.Call) -> bool:
    name = _call_name(node.func)
    if name.rsplit(".", 1)[-1] in {"__import__", "import_module"}:
        return True
    if not isinstance(node.func, ast.Call):
        return False
    inner = node.func
    return (
        isinstance(inner.func, ast.Name)
        and inner.func.id == "getattr"
        and len(inner.args) >= 2
        and isinstance(inner.args[1], ast.Constant)
        and inner.args[1].value == "__import__"
    )


__all__ = [
    "CandidateValidation",
    "CandidateValidationIssue",
    "validate_manim_candidate",
]
