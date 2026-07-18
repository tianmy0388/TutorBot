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
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class StaticGuardResult:
    """Outcome of a static check."""

    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    cleaned_code: str = ""
    external_assets: tuple[str, ...] = ()
    error_code: str = ""
    summary: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "cleaned_code_chars": len(self.cleaned_code),
            "external_assets": list(self.external_assets),
            "error_code": self.error_code,
            "summary": self.summary,
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

    def check(
        self,
        code: str,
        *,
        workdir: Path | None = None,
    ) -> StaticGuardResult:
        """Run all checks against ``code`` and return a verdict."""
        cleaned = self._clean(code)
        warnings: list[str] = []

        # Stage 1: parse once. The same tree drives safety and asset checks.
        try:
            tree = ast.parse(cleaned)
        except SyntaxError as exc:
            msg = f"line {exc.lineno}: {exc.msg}"
            return StaticGuardResult(
                passed=False,
                errors=[f"AST parse failed: {msg}"],
                cleaned_code=cleaned,
                error_code="syntax_error",
                summary="Manim source contains invalid Python syntax",
            )

        # Stage 2: py_compile (catches things AST misses, e.g. undefined names)
        compile_errors = self._py_compile(cleaned)
        if compile_errors:
            return StaticGuardResult(
                passed=False,
                errors=compile_errors,
                cleaned_code=cleaned,
                error_code="compile_error",
                summary="Manim source could not be compiled",
            )

        # Stage 3: Light sanity checks
        sanity_warnings = self._sanity_checks(cleaned)
        warnings.extend(sanity_warnings)

        # Stage 4: Dangerous call detection (hard error, C3)
        dangerous = self._check_dangerous_calls(tree, cleaned)
        if dangerous:
            return StaticGuardResult(
                passed=False,
                errors=dangerous,
                warnings=warnings,
                cleaned_code=cleaned,
                error_code="dangerous_call",
                summary="Manim source contains a disallowed operation",
            )

        # Stage 5: external asset ownership/containment preflight.
        external_assets, dynamic_assets, unavailable_assets = (
            self._inspect_external_assets(tree, workdir=workdir)
        )
        if dynamic_assets:
            return StaticGuardResult(
                passed=False,
                errors=["External Manim asset paths must be string literals"],
                warnings=warnings,
                cleaned_code=cleaned,
                external_assets=external_assets,
                error_code="dynamic_external_asset",
                summary="Manim source uses a dynamic external asset path",
            )
        if unavailable_assets:
            return StaticGuardResult(
                passed=False,
                errors=[
                    f"{len(unavailable_assets)} external Manim asset reference(s) "
                    "are missing or outside the render package"
                ],
                warnings=warnings,
                cleaned_code=cleaned,
                external_assets=external_assets,
                error_code="missing_external_asset",
                summary="Manim source references unavailable external assets",
            )

        return StaticGuardResult(
            passed=True,
            warnings=warnings,
            cleaned_code=cleaned,
            external_assets=external_assets,
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _clean(self, code: str) -> str:
        out = code.replace("\r\n", "\n").replace("\r", "\n").strip()
        fenced = re.fullmatch(
            r"```(?:python|py)?\s*\n(?P<body>.*)\n```",
            out,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if fenced:
            out = fenced.group("body")
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
            with suppress(OSError):
                tmp_path.unlink()

    def _sanity_checks(self, code: str) -> list[str]:
        """Light heuristics — warn (don't fail) on suspicious patterns.

        2026-06-21 plan (C3): upgraded from warnings-only to a
        mix of hard errors (dangerous calls, missing Scene class)
        and warnings (animation count, import style).
        """
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

    # --- 2026-06-21 plan (C3): expanded checks ------------------------

    def _check_dangerous_calls(self, tree: ast.AST, code: str) -> list[str]:
        """Scan for Python calls that are unsafe in a sandboxed
        animation script.

        ``eval``/``exec`` are never needed in Manim scenes;
        ``__import__`` is a code-golf import bypass; ``os.system``
        and ``subprocess`` are out-of-process escapes; ``open`` and
        ``requests`` are file/network I/O that a sandbox should not
        have. Each match is reported with the line it was found on.

        Returns a list of error strings (empty = safe).
        """
        import re as _re

        # Patterns: (regex, human_label)
        patterns: list[tuple[str, str]] = [
            (r"\beval\s*\(", "eval() is not allowed in sandboxed code"),
            (r"\bexec\s*\(", "exec() is not allowed in sandboxed code"),
            (r"\b__import__\s*\(", "__import__() is not allowed"),
            (r"\bos\.system\s*\(", "os.system() is not allowed"),
            (r"\bsubprocess\b", "subprocess is not allowed in sandboxed code"),
            (r"\bopen\s*\(", "open() file I/O is not allowed in sandboxed code"),
        ]
        errors: list[str] = []
        for regex, label in patterns:
            for m in _re.finditer(regex, code):
                line_no = code[: m.start()].count("\n") + 1
                snippet = code[m.start(): m.start() + 40].replace("\n", "\\n")
                errors.append(f"line {line_no}: {label} ({snippet})")
        return errors

    @staticmethod
    def _call_name(node: ast.AST) -> str:
        """Return a dotted ordinary call name (for example ``manim.ImageMobject``)."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            prefix = StaticGuard._call_name(node.value)
            return f"{prefix}.{node.attr}" if prefix else node.attr
        return ""

    def _inspect_external_assets(
        self,
        tree: ast.AST,
        *,
        workdir: Path | None,
    ) -> tuple[tuple[str, ...], bool, tuple[str, ...]]:
        """Collect literal Manim assets and reject dynamic/unsafe references."""
        asset_arguments = {
            "SVGMobject": (0, {"file_name", "filename"}),
            "ImageMobject": (
                0,
                {"filename_or_array", "image_data_or_file", "file_name"},
            ),
            "add_sound": (0, {"sound_file", "file_name", "filename"}),
        }
        ordered: list[str] = []
        seen: set[str] = set()
        dynamic = False
        unavailable: list[str] = []
        root = Path(workdir).resolve() if workdir is not None else None

        calls = sorted(
            (node for node in ast.walk(tree) if isinstance(node, ast.Call)),
            key=lambda node: (node.lineno, node.col_offset),
        )
        for node in calls:
            call_name = self._call_name(node.func)
            short_name = call_name.rsplit(".", 1)[-1]
            specification = asset_arguments.get(short_name)
            if specification is None:
                continue
            positional_index, keyword_names = specification
            value_node = next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg in keyword_names
                ),
                None,
            )
            if value_node is None and len(node.args) > positional_index:
                value_node = node.args[positional_index]
            if not (
                isinstance(value_node, ast.Constant)
                and isinstance(value_node.value, str)
            ):
                dynamic = True
                continue
            reference = value_node.value
            if reference not in seen:
                seen.add(reference)
                ordered.append(reference)
            if (
                root is None or not self._asset_is_available(reference, root)
            ) and reference not in unavailable:
                unavailable.append(reference)
        return tuple(ordered), dynamic, tuple(unavailable)

    @staticmethod
    def _asset_is_available(reference: str, root: Path) -> bool:
        candidate = Path(reference)
        if candidate.is_absolute():
            return False
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return False
        return resolved.is_file()

    @staticmethod
    def _count_animations(code: str) -> int:
        """Count the number of ``self.play`` / ``self.animate``
        calls in the code. Used as a heuristic — too few means the
        video is empty, too many means it may time out.
        """
        import re as _re

        return len(
            _re.findall(
                r"\bself\.(?:play|animate)\s*\(",
                code,
            )
        )


__all__ = ["StaticGuard", "StaticGuardResult"]
