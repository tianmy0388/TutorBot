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
    from tutor.services.learner_profile import reset_profile_builder

    yield data_dir

    # Cleanup: close the store synchronously so the next test
    # starts with a clean slate. The old code called the async
    # ``reset_profile_store()`` without ``await``, returning a
    # coroutine that was never scheduled — the singleton kept
    # its reference to the previous test's temp directory,
    # which we just deleted, causing "unable to open database
    # file" on the next test.
    from tutor.services.learner_profile import _close_profile_store_sync

    _close_profile_store_sync()
    reset_profile_builder()
    shutil.rmtree(data_dir, ignore_errors=True)


@pytest.fixture
def event_loop_policy():
    import asyncio

    return asyncio.DefaultEventLoopPolicy()
