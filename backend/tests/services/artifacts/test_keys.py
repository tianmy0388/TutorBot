from __future__ import annotations

from pathlib import Path

import pytest


def test_artifact_key_survives_data_directory_relocation(tmp_path: Path) -> None:
    from tutor.services.artifacts.keys import resolve_artifact_key, to_artifact_key

    old = tmp_path / "old"
    image = old / "artifacts" / "p1" / "figure_1.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")

    key = to_artifact_key(image, old)

    assert key == "artifacts/p1/figure_1.png"
    assert resolve_artifact_key(key, tmp_path / "new") == (
        tmp_path / "new" / "artifacts" / "p1" / "figure_1.png"
    )


@pytest.mark.parametrize(
    "key",
    [
        "../secret.txt",
        "artifacts/../../secret.txt",
        "/etc/passwd",
        "C:/Windows/System32/config/SAM",
        r"..\secret.txt",
    ],
)
def test_artifact_key_rejects_traversal_and_absolute_paths(
    tmp_path: Path, key: str
) -> None:
    from tutor.services.artifacts.keys import UnsafeArtifactKey, resolve_artifact_key

    with pytest.raises(UnsafeArtifactKey):
        resolve_artifact_key(key, tmp_path)


def test_to_artifact_key_rejects_paths_outside_data_directory(tmp_path: Path) -> None:
    from tutor.services.artifacts.keys import UnsafeArtifactKey, to_artifact_key

    with pytest.raises(UnsafeArtifactKey):
        to_artifact_key(tmp_path / "outside.txt", tmp_path / "data")
