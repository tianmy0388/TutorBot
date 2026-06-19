"""Pytest configuration.

We use an isolated in-memory SQLite database per test (via a tmpfile path,
since aiosqlite doesn't easily support :memory: in async context).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Force a deterministic test settings before any tutor module is imported.
os.environ.setdefault("TUTOR_ENV", "test")
os.environ.setdefault("TUTOR_LOG_LEVEL", "WARNING")
os.environ.setdefault("TUTOR_DATA_DIR", str(Path(tempfile.gettempdir()) / "tutor_test"))


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """Give each test its own data dir + clear singletons."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("TUTOR_DATA_DIR", str(data_dir))
    # Clear settings cache so the env-var override takes effect.
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()

    # Reset profile-store / builder singletons.
    from tutor.services.learner_profile import (
        reset_profile_builder,
        reset_profile_store,
    )

    yield data_dir

    # Cleanup
    try:
        # Stop the loop and close the store if it was created.
        import asyncio

        async def _close():
            from tutor.services.learner_profile.store import get_profile_store

            store = get_profile_store()
            await store.close()

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_close())
        finally:
            loop.close()
    except Exception:
        pass
    reset_profile_builder()
    reset_profile_store()
    shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture
def event_loop_policy():
    import asyncio

    return asyncio.DefaultEventLoopPolicy()
