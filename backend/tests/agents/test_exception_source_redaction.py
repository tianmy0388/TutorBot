"""Static backstop: exception objects cannot enter nested-agent public surfaces."""

from __future__ import annotations

import ast
from pathlib import Path

AGENTS_ROOT = Path(__file__).parents[2] / "tutor" / "agents"


def _loads_name(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(child, ast.Name)
        and isinstance(child.ctx, ast.Load)
        and child.id == name
        for child in ast.walk(node)
    )


def _is_name(node: ast.AST, name: str) -> bool:
    return isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id == name


def test_agent_exception_handlers_do_not_render_raw_exception_objects() -> None:
    violations: list[str] = []
    for path in sorted(AGENTS_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "logger"
                and node.func.attr == "exception"
            ):
                violations.append(
                    f"{path.relative_to(AGENTS_ROOT)}:{node.lineno}: logger.exception"
                )
            if not isinstance(node, ast.ExceptHandler) or not node.name:
                continue
            exc_name = node.name
            for child in ast.walk(node):
                if isinstance(child, ast.FormattedValue) and _loads_name(child.value, exc_name):
                    violations.append(
                        f"{path.relative_to(AGENTS_ROOT)}:{child.lineno}: formatted {exc_name}"
                    )
                if (
                    isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Name)
                    and child.func.id in {"str", "repr"}
                    and any(_loads_name(arg, exc_name) for arg in child.args)
                ):
                    violations.append(
                        f"{path.relative_to(AGENTS_ROOT)}:{child.lineno}: {child.func.id}({exc_name})"
                    )
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
                    is_public_sink = (
                        isinstance(child.func.value, ast.Name)
                        and child.func.value.id in {"logger", "stream", "report"}
                    )
                    direct_values = [*child.args, *(kw.value for kw in child.keywords)]
                    if is_public_sink and any(
                        _is_name(value, exc_name) for value in direct_values
                    ):
                        violations.append(
                            f"{path.relative_to(AGENTS_ROOT)}:{child.lineno}: "
                            f"raw {exc_name} passed to public sink"
                        )
                if (
                    isinstance(child, ast.Return)
                    and child.value is not None
                    and _is_name(child.value, exc_name)
                ):
                    violations.append(
                        f"{path.relative_to(AGENTS_ROOT)}:{child.lineno}: returned {exc_name}"
                    )
    assert not violations, "raw exception rendering found:\n" + "\n".join(violations)
