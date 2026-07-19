"""Safe conversion between persisted artifact keys and runtime paths."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


class UnsafeArtifactKey(ValueError):
    """Raised when an artifact reference could escape ``data_dir``."""


def to_artifact_key(path: Path, data_dir: Path) -> str:
    """Return a relative POSIX key for a path contained by ``data_dir``."""
    root = data_dir.resolve()
    try:
        relative = path.resolve().relative_to(root)
    except ValueError as exc:
        raise UnsafeArtifactKey(str(path)) from exc
    key = relative.as_posix()
    _validate_key(key)
    return key


def resolve_artifact_key(key: str, data_dir: Path) -> Path:
    """Resolve a persisted key below ``data_dir`` without permitting traversal."""
    _validate_key(key)
    root = data_dir.resolve()
    candidate = root.joinpath(*PurePosixPath(key).parts).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise UnsafeArtifactKey(key) from exc
    return candidate


def _validate_key(key: str) -> None:
    if not isinstance(key, str) or not key or "\\" in key or "\x00" in key:
        raise UnsafeArtifactKey(str(key))
    pure = PurePosixPath(key)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise UnsafeArtifactKey(key)
    # PurePosixPath treats ``C:/...`` as relative, even on Windows.
    if pure.parts and ":" in pure.parts[0]:
        raise UnsafeArtifactKey(key)


__all__ = ["UnsafeArtifactKey", "resolve_artifact_key", "to_artifact_key"]
