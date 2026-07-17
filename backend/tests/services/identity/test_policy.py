from __future__ import annotations

import sqlite3

import pytest
from tutor.services.identity import LOCAL_USER_ID, IdentityPolicy, IdentityRequired


def test_single_user_mode_ignores_stale_browser_identity() -> None:
    policy = IdentityPolicy(multi_user_enabled=False)

    assert policy.resolve("u_664b09a5103745d6") == LOCAL_USER_ID


def test_multi_user_mode_requires_identity() -> None:
    policy = IdentityPolicy(multi_user_enabled=True)

    with pytest.raises(IdentityRequired, match="user_id is required"):
        policy.resolve(None)


def test_multi_user_mode_preserves_explicit_identity() -> None:
    policy = IdentityPolicy(multi_user_enabled=True)

    assert policy.resolve("u_alice") == "u_alice"


def test_migration_canonicalizes_all_ownership_columns_idempotently(tmp_path) -> None:
    from tutor.services.migration.local_single_user import run_local_migration

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    legacy_dir = tmp_path / "backend" / "data"
    legacy_dir.mkdir(parents=True)
    database = legacy_dir / "ownership.db"
    tables = (
        ("conversations", "user_id"),
        ("messages", "owner_user_id"),
        ("jobs", "user_id"),
        ("packages", "owner_user_id"),
        ("profiles", "user_id"),
        ("learning_events", "owner_user_id"),
    )
    with sqlite3.connect(database) as connection:
        for table, owner_column in tables:
            connection.execute(
                f'CREATE TABLE "{table}" '
                f'(id TEXT PRIMARY KEY, "{owner_column}" TEXT NOT NULL, created_at TEXT NOT NULL)'
            )
            connection.execute(
                f'INSERT INTO "{table}" VALUES (?, ?, ?)',
                (f"{table}-1", "u_legacy", "2026-07-17T00:00:00Z"),
            )

    first = run_local_migration(tmp_path, LOCAL_USER_ID, dry_run=False)
    second = run_local_migration(tmp_path, LOCAL_USER_ID, dry_run=False)

    with sqlite3.connect(data_dir / "ownership.db") as connection:
        for table, owner_column in tables:
            assert connection.execute(f'SELECT "{owner_column}", created_at FROM "{table}"').fetchone() == (
                LOCAL_USER_ID,
                "2026-07-17T00:00:00Z",
            )
    assert first.written_files == 1
    assert second.written_files == 0
