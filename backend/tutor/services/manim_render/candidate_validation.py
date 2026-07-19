"""Deterministic validation for complete LLM-generated Manim candidates."""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from tutor.services.manim_render.static_guard import StaticGuard


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
    locally_defined = _defined_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        call_name = _call_name(node.func)
        if call_name.rsplit(".", 1)[-1] == "VGroup":
            for argument in node.args:
                if isinstance(argument, ast.Attribute):
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
        elif isinstance(node, ast.Import):
            names.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")
    return names


__all__ = [
    "CandidateValidation",
    "CandidateValidationIssue",
    "validate_manim_candidate",
]
