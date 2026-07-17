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


def _make_client() -> TestClient:
    app = FastAPI()
    app.state.settings = SimpleNamespace(multi_user_enabled=True)
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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-xvs"]))
