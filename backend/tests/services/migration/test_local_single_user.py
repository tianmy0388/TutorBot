from __future__ import annotations

import sqlite3

import pytest
import tutor.services.config.settings as settings_module
from tutor.services.config.settings import Settings


def test_relative_data_dir_resolves_from_repo_root(tmp_path, monkeypatch):
    repo = tmp_path / "TutorBot"
    fake_settings_file = repo / "backend" / "tutor" / "services" / "config" / "settings.py"
    fake_settings_file.parent.mkdir(parents=True)
    monkeypatch.setattr(settings_module, "__file__", str(fake_settings_file))

    settings = Settings(data_dir="./data", _env_file=None)

    assert settings_module._repo_root() == repo
    assert settings.data_dir == repo / "data"


def test_dry_run_lists_sources_without_writing(tmp_path):
    try:
        from tutor.services.migration.local_single_user import run_local_migration
    except ModuleNotFoundError:
        pytest.fail("local single-user migration module is missing")

    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "backend" / "data").mkdir(parents=True)

    report = run_local_migration(tmp_path, "local-user", dry_run=True)

    assert report.source_dirs == (
        (tmp_path / "data").resolve(),
        (tmp_path / "backend" / "data").resolve(),
    )
    assert report.target_dir == (tmp_path / "data").resolve()
    assert report.backup_dir is None
    assert report.written_files == 0


def test_report_discovers_sorted_user_ids_from_sqlite_files(tmp_path):
    try:
        from tutor.services.migration.local_single_user import build_migration_report
    except ModuleNotFoundError:
        pytest.fail("local single-user migration module is missing")

    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    with sqlite3.connect(data_dir / "conversations.db") as connection:
        connection.execute("CREATE TABLE conversations (session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO conversations VALUES (?, ?)",
            (("session-1", "u-zeta"), ("session-2", "u-alpha")),
        )

    report = build_migration_report(tmp_path, "local-user")

    assert report.discovered_users == ("u-alpha", "u-zeta")


def test_migration_backs_up_before_copying_and_is_repeatable(tmp_path):
    try:
        from tutor.services.migration.local_single_user import run_local_migration
    except ModuleNotFoundError:
        pytest.fail("local single-user migration module is missing")

    canonical = tmp_path / "data"
    legacy = tmp_path / "backend" / "data"
    canonical.mkdir(exist_ok=True)
    legacy.mkdir(parents=True)
    (canonical / "canonical.txt").write_text("canonical", encoding="utf-8")
    (legacy / "legacy.txt").write_text("legacy", encoding="utf-8")

    first = run_local_migration(tmp_path, "local-user", dry_run=False)

    assert first.backup_dir is not None
    assert (first.backup_dir / "data" / "canonical.txt").read_text(encoding="utf-8") == "canonical"
    assert (first.backup_dir / "backend" / "data" / "legacy.txt").read_text(encoding="utf-8") == "legacy"
    assert (canonical / "legacy.txt").read_text(encoding="utf-8") == "legacy"
    assert (legacy / "legacy.txt").read_text(encoding="utf-8") == "legacy"
    assert first.written_files == 1

    second = run_local_migration(tmp_path, "local-user", dry_run=False)

    assert second.backup_dir is not None
    assert second.backup_dir != first.backup_dir
    assert second.written_files == 0
    assert (legacy / "legacy.txt").exists()


def test_migration_merges_sqlite_rows_without_mutating_legacy_source(tmp_path):
    from tutor.services.migration.local_single_user import run_local_migration

    canonical = tmp_path / "data"
    legacy = tmp_path / "backend" / "data"
    canonical.mkdir(exist_ok=True)
    legacy.mkdir(parents=True)
    for database, row in (
        (canonical / "conversations.db", ("session-root", "u-root")),
        (legacy / "conversations.db", ("session-legacy", "u-legacy")),
    ):
        with sqlite3.connect(database) as connection:
            connection.execute(
                "CREATE TABLE conversations (session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL)"
            )
            connection.execute("INSERT INTO conversations VALUES (?, ?)", row)

    report = run_local_migration(tmp_path, "local-user", dry_run=False)

    with sqlite3.connect(canonical / "conversations.db") as connection:
        rows = connection.execute(
            "SELECT session_id, user_id FROM conversations ORDER BY session_id"
        ).fetchall()
    with sqlite3.connect(legacy / "conversations.db") as connection:
        legacy_rows = connection.execute("SELECT session_id, user_id FROM conversations").fetchall()

    assert rows == [
        ("session-legacy", "local-user"),
        ("session-root", "local-user"),
    ]
    assert legacy_rows == [("session-legacy", "u-legacy")]
    assert report.backup_dir is not None
    with sqlite3.connect(report.backup_dir / "data" / "conversations.db") as connection:
        assert connection.execute("SELECT user_id FROM conversations").fetchone() == ("u-root",)
