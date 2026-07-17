"""Regression test: GET .../resources/{resource_id}/artifacts/{name}

**2026-07-08 fix (585f367d trace):** pre-fix, the code sandbox saved
matplotlib figures to ``data_dir/code_runs/<run_id>/figure_*.png`` and
attached their paths to ``format_specific.artifacts[]``, but the
frontend ``CodeViewer`` could not display them — there was no HTTP
route that would turn the absolute filesystem path into a URL the
browser could fetch. The new endpoint streams the file via FastAPI
``FileResponse``.

Security contract under test:
  * 404 when the package / resource / artifact doesn't exist
  * 403 when the resolved file path escapes ``data_dir`` (anti-traversal)
  * 200 with the right ``media_type`` when everything lines up
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Load the resources router bypassing ``tutor.api.__init__`` (which
# triggers the full app, including unrelated modules that fail to
# import in the test environment).
_RESOURCES_PATH = Path(__file__).resolve().parents[2] / "tutor" / "api" / "routers" / "resources.py"
_spec = importlib.util.spec_from_file_location("_resources_router_under_test", _RESOURCES_PATH)
_resources_module = importlib.util.module_from_spec(_spec)
sys.modules["_resources_router_under_test"] = _resources_module
_spec.loader.exec_module(_resources_module)
resources_router = _resources_module.router
# Aliases to module-level singletons used inside the router.
_resources_module_pkg = _resources_module


def _make_client(*, multi_user_enabled: bool = True) -> TestClient:
    app = FastAPI()
    app.state.settings = SimpleNamespace(multi_user_enabled=multi_user_enabled)
    app.include_router(resources_router, prefix="/api/v1")
    return TestClient(app)


@pytest.fixture
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Yield a :class:`ResourcePackageStore` rooted at a fresh tmp
    DB. We also patch the singleton accessor the router uses so the
    endpoint reads from this test store, not the global one.

    ``get_resource_package_store`` is ``@lru_cache``d via the
    ``get_settings`` chain, so we monkeypatch the *function the
    router imports* (``tutor.api.routers.resources.get_resource_package_store``)
    directly.
    """
    db_path = tmp_path / "resource_packages.db"
    from tutor.services.resource_package.store import (
        ResourcePackageStore,
        reset_resource_package_store,
    )

    # Drop any cached singleton from previous tests.
    reset_resource_package_store()

    store = ResourcePackageStore(db_path=db_path)
    monkeypatch.setattr(
        _resources_module,
        "get_resource_package_store",
        lambda: store,
    )
    return store


@pytest.mark.asyncio
async def test_artifact_endpoint_serves_png_inside_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_store,
) -> None:
    """Happy path: a PNG artifact declared on a resource is served
    with the correct media-type."""
    from tutor.services.config.settings import get_settings

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)

    # Build a sandbox artifact: PNG file inside data_dir/code_runs/run_X/figure_1.png
    art_dir = data_dir / "code_runs" / "run_X"
    art_dir.mkdir(parents=True)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    png_path = art_dir / "figure_1.png"
    png_path.write_bytes(png_bytes)

    from tutor.services.resource_package.schema import (
        Resource,
        ResourcePackage,
        ResourceType,
    )

    await isolated_store.init()

    resource = Resource(
        type=ResourceType.CODE,
        title="反向传播 XOR",
        content="<markdown>",
        topic="反向传播",
        format_specific={
            "language": "python",
            "code": "print('hi')",
            "execution_status": "success",
            "artifacts": [
                {"name": "figure_1.png", "path": str(png_path), "kind": "png"},
            ],
        },
    )
    pkg = ResourcePackage(topic="反向传播", resources=[resource])
    pkg.metadata["user_id"] = "u-test"
    pkg.metadata["session_id"] = "s-test"
    await isolated_store.save(pkg, user_id="u-test")

    client = _make_client()
    resp = client.get(
        f"/api/v1/resources/packages/u-test/{pkg.package_id}/resources/{resource.resource_id}/artifacts/figure_1.png"
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content == png_bytes


@pytest.mark.asyncio
async def test_artifact_endpoint_404_when_artifact_not_in_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_store,
) -> None:
    """Requesting an artifact name that's not in the resource's
    ``format_specific.artifacts[]`` must 404."""
    from tutor.services.config.settings import get_settings
    from tutor.services.resource_package.schema import (
        Resource,
        ResourcePackage,
        ResourceType,
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)

    await isolated_store.init()

    resource = Resource(
        type=ResourceType.CODE,
        title="t",
        content="",
        topic="t",
        format_specific={"artifacts": []},
    )
    pkg = ResourcePackage(topic="t", resources=[resource])
    pkg.metadata["user_id"] = "u-test"
    await isolated_store.save(pkg, user_id="u-test")

    client = _make_client()
    resp = client.get(
        f"/api/v1/resources/packages/u-test/{pkg.package_id}/resources/{resource.resource_id}/artifacts/figure_99.png"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_artifact_endpoint_403_for_path_outside_data_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_store,
) -> None:
    """Anti-traversal: even if a tampered manifest names a file
    outside ``data_dir`` (e.g. ``/etc/passwd``), the endpoint must
    reject it."""
    from tutor.services.config.settings import get_settings
    from tutor.services.resource_package.schema import (
        Resource,
        ResourcePackage,
        ResourceType,
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)

    await isolated_store.init()

    resource = Resource(
        type=ResourceType.CODE,
        title="t",
        content="",
        topic="t",
        format_specific={
            "artifacts": [
                {
                    "name": "passwd",
                    "path": "/etc/passwd",
                    "kind": "txt",
                },
            ]
        },
    )
    pkg = ResourcePackage(topic="t", resources=[resource])
    pkg.metadata["user_id"] = "u-test"
    await isolated_store.save(pkg, user_id="u-test")

    # Sanity: the save round-trips. If this fails, the test below
    # can't tell whether the failure is in the save layer or the
    # route.
    loaded_pkg = await isolated_store.get(pkg.package_id)
    assert loaded_pkg is not None, "save() did not persist the package"
    loaded_res = await isolated_store.get_resource(resource.resource_id)
    assert loaded_res is not None, "save() did not persist the resource"
    assert loaded_res.format_specific.get("artifacts"), "format_specific.artifacts was not round-tripped"

    client = _make_client()
    resp = client.get(
        f"/api/v1/resources/packages/u-test/{pkg.package_id}/resources/{resource.resource_id}/artifacts/passwd"
    )
    assert resp.status_code == 403, f"expected 403 for traversal; got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_artifact_endpoint_works_without_package_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_store,
) -> None:
    """**2026-07-08 fix (039b4a70 trace):** when the frontend only
    has a placeholder package_id (e.g. ``"pending-${job_id}"`` or
    ``"_"``) — typical after a 600s timeout where the capability
    never reached the persistence stage — the package-scoped
    endpoint 404's. The new package-less endpoint
    ``GET /resources/{user_id}/resources/{resource_id}/artifacts/{name}``
    resolves by resource_id alone (which is globally unique) and
    only checks user_id against the resource row.
    """
    from tutor.services.config.settings import get_settings
    from tutor.services.resource_package.schema import (
        Resource,
        ResourcePackage,
        ResourceType,
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)

    art_dir = data_dir / "code_runs" / "run_X"
    art_dir.mkdir(parents=True)
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
    png_path = art_dir / "figure_1.png"
    png_path.write_bytes(png_bytes)

    await isolated_store.init()

    resource = Resource(
        type=ResourceType.CODE,
        title="反向传播 XOR",
        content="<markdown>",
        topic="反向传播",
        format_specific={
            "language": "python",
            "code": "print('hi')",
            "execution_status": "success",
            "artifacts": [
                {"name": "figure_1.png", "path": str(png_path), "kind": "png"},
            ],
        },
    )
    pkg = ResourcePackage(topic="反向传播", resources=[resource])
    pkg.metadata["user_id"] = "u-test"
    await isolated_store.save(pkg, user_id="u-test")

    client = _make_client()
    resp = client.get(
        # NOTE: no package_id in the URL — that's the whole point.
        f"/api/v1/resources/u-test/resources/{resource.resource_id}/artifacts/figure_1.png"
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content == png_bytes


@pytest.mark.asyncio
async def test_artifact_endpoint_package_less_404_wrong_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    isolated_store,
) -> None:
    """The package-less endpoint must still 404 when user_id
    doesn't match the resource row — it just skips the package
    lookup, not the user check."""
    from tutor.services.config.settings import get_settings
    from tutor.services.resource_package.schema import (
        Resource,
        ResourcePackage,
        ResourceType,
    )

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)

    art_dir = data_dir / "code_runs" / "run_X"
    art_dir.mkdir(parents=True)
    png_path = art_dir / "figure_1.png"
    png_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    await isolated_store.init()
    resource = Resource(
        type=ResourceType.CODE,
        title="t",
        content="",
        topic="t",
        format_specific={
            "artifacts": [
                {"name": "figure_1.png", "path": str(png_path), "kind": "png"},
            ],
        },
    )
    pkg = ResourcePackage(topic="t", resources=[resource])
    pkg.metadata["user_id"] = "u-test"
    await isolated_store.save(pkg, user_id="u-test")

    client = _make_client()
    resp = client.get(f"/api/v1/resources/u-WRONG/resources/{resource.resource_id}/artifacts/figure_1.png")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_local_mode_serves_historical_owner_artifacts_from_both_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_store
) -> None:
    from tutor.services.config.settings import get_settings
    from tutor.services.resource_package.schema import Resource, ResourcePackage, ResourceType

    data_dir = tmp_path / "data"
    artifact = data_dir / "legacy" / "figure.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"legacy")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)
    await isolated_store.init()
    resource = Resource(
        type=ResourceType.CODE,
        title="legacy",
        format_specific={
            "artifacts": [{"name": "figure.png", "path": str(artifact), "kind": "png"}]
        },
    )
    package = ResourcePackage(topic="legacy", resources=[resource])
    await isolated_store.save(package, user_id="historical-owner")
    client = _make_client(multi_user_enabled=False)

    scoped = client.get(
        f"/api/v1/resources/packages/stale-browser/{package.package_id}/resources/"
        f"{resource.resource_id}/artifacts/figure.png"
    )
    package_less = client.get(
        f"/api/v1/resources/stale-browser/resources/{resource.resource_id}/artifacts/figure.png"
    )

    assert scoped.status_code == 200, scoped.text
    assert package_less.status_code == 200, package_less.text
    assert scoped.content == package_less.content == b"legacy"


@pytest.mark.asyncio
async def test_multi_user_mode_denies_historical_owner_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_store
) -> None:
    from tutor.services.config.settings import get_settings
    from tutor.services.resource_package.schema import Resource, ResourcePackage, ResourceType

    data_dir = tmp_path / "data"
    artifact = data_dir / "legacy" / "figure.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"legacy")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)
    await isolated_store.init()
    resource = Resource(
        type=ResourceType.CODE,
        title="legacy",
        format_specific={"artifacts": [{"name": "figure.png", "path": str(artifact)}]},
    )
    package = ResourcePackage(topic="legacy", resources=[resource])
    await isolated_store.save(package, user_id="owner-a")
    attacker_package = ResourcePackage(topic="attacker", resources=[])
    await isolated_store.save(attacker_package, user_id="owner-b")
    client = _make_client(multi_user_enabled=True)

    scoped = client.get(
        f"/api/v1/resources/packages/owner-b/{package.package_id}/resources/"
        f"{resource.resource_id}/artifacts/figure.png"
    )
    package_less = client.get(
        f"/api/v1/resources/owner-b/resources/{resource.resource_id}/artifacts/figure.png"
    )
    mismatched_join = client.get(
        f"/api/v1/resources/packages/owner-b/{attacker_package.package_id}/resources/"
        f"{resource.resource_id}/artifacts/figure.png"
    )

    assert scoped.status_code == 404
    assert package_less.status_code == 404
    assert mismatched_join.status_code == 404


@pytest.mark.asyncio
async def test_legacy_url_normalizes_local_but_preserves_external(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_store
) -> None:
    from tutor.services.config.settings import get_settings
    from tutor.services.resource_package.schema import Resource, ResourcePackage, ResourceType

    data_dir = tmp_path / "data"
    local = data_dir / "code_runs" / "run" / "figure.png"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"local")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)
    await isolated_store.init()
    resource = Resource(
        type=ResourceType.CODE,
        title="urls",
        format_specific={
            "artifacts": [
                {"name": "figure.png", "url": "code_runs/run/figure.png"},
                {"name": "external.png", "url": "https://cdn.example.com/external.png"},
            ]
        },
    )
    package = ResourcePackage(topic="urls", resources=[resource])
    await isolated_store.save(package, user_id="owner")

    loaded = await isolated_store.get_resource(resource.resource_id)
    assert loaded is not None
    local_entry, external_entry = loaded.format_specific["artifacts"]
    assert local_entry["artifact_key"] == "code_runs/run/figure.png"
    assert "url" not in local_entry
    assert external_entry["url"] == "https://cdn.example.com/external.png"
    assert "artifact_key" not in external_entry

    client = _make_client()
    local_response = client.get(
        f"/api/v1/resources/owner/resources/{resource.resource_id}/artifacts/figure.png"
    )
    external_response = client.get(
        f"/api/v1/resources/owner/resources/{resource.resource_id}/artifacts/external.png"
    )
    assert local_response.status_code == 200
    assert local_response.content == b"local"
    assert external_response.status_code == 404


@pytest.mark.asyncio
async def test_ppt_download_local_mode_accepts_historical_owner_but_multi_denies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, isolated_store
) -> None:
    from tutor.services.config.settings import get_settings
    from tutor.services.resource_package.schema import Resource, ResourcePackage, ResourceType

    data_dir = tmp_path / "data"
    pptx = data_dir / "ppt" / "legacy-package" / "deck.pptx"
    pptx.parent.mkdir(parents=True)
    pptx.write_bytes(b"pptx")
    get_settings.cache_clear()
    monkeypatch.setattr(get_settings(), "data_dir", data_dir, raising=False)
    await isolated_store.init()
    resource = Resource(
        resource_id="ppt-download-resource",
        type=ResourceType.PPT,
        title="Legacy deck",
        format_specific={"pptx_path": str(pptx), "slide_count": 1},
    )
    package = ResourcePackage(
        package_id="legacy-package",
        topic="legacy",
        resources=[resource],
    )
    await isolated_store.save(package, user_id="historical-owner")
    path = (
        f"/api/v1/resources/packages/stale-browser/{package.package_id}/resources/"
        f"{resource.resource_id}/download"
    )

    local_response = _make_client(multi_user_enabled=False).get(path)
    multi_response = _make_client(multi_user_enabled=True).get(path)

    assert local_response.status_code == 200, local_response.text
    assert local_response.content == b"pptx"
    assert multi_response.status_code == 404


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))
