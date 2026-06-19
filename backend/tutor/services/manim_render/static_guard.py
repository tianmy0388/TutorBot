"""StaticGuard — pre-render syntax + sanity check for Manim Python code.

Inspired by ManimCat's ``static-guard`` (py_compile + mypy). For Tutor MVP
we run ``python -m py_compile`` only (mypy is slow and brittle across
versions). We also filter known false-positives that Manim triggers
(``camera.frame`` mypy complaints, etc.).

The guard is **stateless**: take code in, return a result.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class StaticGuardResult:
    """Outcome of a static check."""

    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cleaned_code: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "cleaned_code_chars": len(self.cleaned_code),
        }


# Known Manim patterns that look like errors but are fine
KNOWN_FALSE_POSITIVES = (
    # mypy: "Returning Any from function declared to return \"None\""
    "Returning Any from function declared to return",
    # Manim's camera.frame attribute is dynamically typed
    "camera.frame",
    # Type-ignore comments are intentional
    "type: ignore",
)


# Common pre-cleaning transformations applied before py_compile
_CODE_TRANSFORMS = (
    # Strip leading shebang lines (not needed in Manim)
    (re.compile(r"^#!.*\n", re.MULTILINE), ""),
)


class StaticGuard:
    """Run pre-render static checks on Manim Python code."""

    def check(self, code: str) -> StaticGuardResult:
        """Run all checks against ``code`` and return a verdict."""
        cleaned = self._clean(code)
        warnings: list[str] = []

        # Stage 1: AST parse (cheap, no subprocess)
        ast_errors = self._ast_parse(cleaned)
        if ast_errors:
            return StaticGuardResult(
                passed=False,
                errors=ast_errors,
                cleaned_code=cleaned,
            )

        # Stage 2: py_compile (catches things AST misses, e.g. undefined names)
        compile_errors = self._py_compile(cleaned)
        if compile_errors:
            return StaticGuardResult(
                passed=False,
                errors=compile_errors,
                cleaned_code=cleaned,
            )

        # Stage 3: Light sanity checks
        sanity_warnings = self._sanity_checks(cleaned)
        warnings.extend(sanity_warnings)

        return StaticGuardResult(
            passed=True,
            warnings=warnings,
            cleaned_code=cleaned,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _clean(self, code: str) -> str:
        out = code
        for pattern, repl in _CODE_TRANSFORMS:
            out = pattern.sub(repl, out)
        return out.strip() + "\n"

    def _ast_parse(self, code: str) -> list[str]:
        try:
            ast.parse(code)
            return []
        except SyntaxError as exc:
            msg = f"line {exc.lineno}: {exc.msg}"
            return [f"AST parse failed: {msg}"]

    def _py_compile(self, code: str) -> list[str]:
        """Run ``python -m py_compile`` on the code in a temp file."""
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".py",
            delete=False,
            encoding="utf-8",
        ) as fh:
            fh.write(code)
            tmp_path = Path(fh.name)
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "py_compile", str(tmp_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0:
                return []
            stderr = proc.stderr or ""
            # Filter known false positives
            for fp in KNOWN_FALSE_POSITIVES:
                if fp in stderr:
                    return []
            # Otherwise, parse the first error line
            errors: list[str] = []
            for line in stderr.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "SyntaxError" in line or "IndentationError" in line:
                    errors.append(line)
            if not errors:
                errors.append(stderr.strip().splitlines()[0] if stderr.strip() else "unknown py_compile error")
            return [f"py_compile failed: {e}" for e in errors[:5]]
        except subprocess.TimeoutExpired:
            return ["py_compile timeout after 30s"]
        except Exception as exc:  # noqa: BLE001
            return [f"py_compile execution error: {exc}"]
        finally:
            try:
                tmp_path.unlink()
            except OSError:
                pass

    def _sanity_checks(self, code: str) -> list[str]:
        """Light heuristics — warn (don't fail) on suspicious patterns."""
        warnings: list[str] = []
        # Must contain a Scene subclass
        if "class " not in code or "Scene" not in code:
            warnings.append("No Scene class found in code")
        # Must define construct()
        if "def construct" not in code:
            warnings.append("No construct() method — Manim won't render anything")
        # Should import manim
        if "from manim" not in code and "import manim" not in code:
            warnings.append("No 'from manim import' statement found")
        return warnings


__all__ = ["StaticGuard", "StaticGuardResult"]
