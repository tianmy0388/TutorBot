"""Portable references to files stored below TutorBot's data directory."""

from tutor.services.artifacts.keys import (
    UnsafeArtifactKey,
    resolve_artifact_key,
    to_artifact_key,
)

__all__ = ["UnsafeArtifactKey", "resolve_artifact_key", "to_artifact_key"]
