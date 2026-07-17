from __future__ import annotations

import shutil
import sqlite3

import pytest
import tutor.services.config.settings as settings_module
from tutor.services.config.settings import Settings
from typer.testing import CliRunner


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


@pytest.mark.parametrize("target_exists", [False, True])
def test_migration_preserves_committed_wal_rows(tmp_path, target_exists):
    from tutor.services.migration.local_single_user import run_local_migration

    canonical = tmp_path / "data"
    legacy = tmp_path / "backend" / "data"
    canonical.mkdir(exist_ok=True)
    legacy.mkdir(parents=True)
    database_name = "wal-events.db"

    if target_exists:
        with sqlite3.connect(legacy / database_name) as connection:
            connection.execute("CREATE TABLE events (event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL)")
            connection.execute("INSERT INTO events VALUES ('canonical', 'u-root')")

    writer = sqlite3.connect(legacy / database_name)
    try:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint = 0")
        if target_exists:
            shutil.copy2(legacy / database_name, canonical / database_name)
        writer.execute("CREATE TABLE IF NOT EXISTS events (event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL)")
        writer.execute("INSERT INTO events VALUES ('from-wal', 'u-legacy')")
        writer.commit()
        assert (legacy / f"{database_name}-wal").exists()

        report = run_local_migration(tmp_path, "local-user", dry_run=False)

        try:
            with sqlite3.connect(canonical / database_name) as connection:
                rows = connection.execute("SELECT event_id, user_id FROM events ORDER BY event_id").fetchall()
        except sqlite3.OperationalError:
            rows = []
        assert report.backup_dir is not None
        with sqlite3.connect(report.backup_dir / "backend" / "data" / database_name) as connection:
            backup_rows = connection.execute(
                "SELECT event_id, user_id FROM events ORDER BY event_id"
            ).fetchall()
    finally:
        writer.close()

    expected = [("from-wal", "local-user")]
    if target_exists:
        expected.insert(0, ("canonical", "local-user"))
    assert rows == expected
    expected_backup = [("from-wal", "u-legacy")]
    if target_exists:
        expected_backup.insert(0, ("canonical", "u-root"))
    assert backup_rows == expected_backup


def test_user_rewrite_deduplicates_composite_unique_collisions(tmp_path):
    from tutor.services.migration.local_single_user import run_local_migration

    canonical = tmp_path / "data"
    canonical.mkdir(exist_ok=True)
    database = canonical / "ownership.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE memberships ("
            "id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, scope TEXT NOT NULL, "
            "payload TEXT NOT NULL, UNIQUE(user_id, scope))"
        )
        connection.executemany(
            "INSERT INTO memberships VALUES (?, ?, ?, ?)",
            (
                (1, "local-user", "shared", "canonical"),
                (2, "u-legacy", "shared", "duplicate"),
                (3, "u-legacy", "legacy-only", "preserved"),
            ),
        )

    report = run_local_migration(tmp_path, "local-user", dry_run=False)

    with sqlite3.connect(database) as connection:
        rows = connection.execute("SELECT user_id, scope, payload FROM memberships ORDER BY scope").fetchall()
        legacy_count = connection.execute(
            "SELECT COUNT(*) FROM memberships WHERE user_id != 'local-user'"
        ).fetchone()[0]
    assert rows == [
        ("local-user", "legacy-only", "preserved"),
        ("local-user", "shared", "canonical"),
    ]
    assert legacy_count == 0
    assert report.backup_dir is not None
    with sqlite3.connect(report.backup_dir / "data" / "ownership.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM memberships").fetchone() == (3,)


@pytest.mark.parametrize("target_user_id", ["anonymous", "LOCAL-USER", " local-user "])
def test_service_rejects_noncanonical_target_user_ids(tmp_path, target_user_id):
    from tutor.services.migration.local_single_user import build_migration_report

    with pytest.raises(ValueError, match="exactly 'local-user'"):
        build_migration_report(tmp_path, target_user_id)


def test_cli_rejects_noncanonical_target_user_id(tmp_path):
    from tutor.cli.main import app

    result = CliRunner().invoke(
        app,
        [
            "migrate-local-data",
            "--repo-root",
            str(tmp_path),
            "--target-user-id",
            "anonymous",
            "--dry-run",
        ],
    )

    assert result.exit_code == 2
    assert "--target-user-id must be exactly 'local-user'" in result.output


def test_migration_normalizes_known_artifact_paths_and_reports_external_paths(tmp_path):
    from tutor.services.migration.local_single_user import run_local_migration

    canonical = tmp_path / "data"
    legacy = tmp_path / "backend" / "data"
    canonical.mkdir(exist_ok=True)
    legacy.mkdir(parents=True)
    artifact = legacy / "code_runs" / "run-1" / "figure.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"png")
    external = (tmp_path.parent / "external" / "video.mp4").resolve()
    prose = f"Rendered artifact is stored at {artifact.resolve()}"

    with sqlite3.connect(legacy / "resources.db") as connection:
        connection.execute(
            "CREATE TABLE resources ("
            "resource_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, "
            "artifact_path TEXT NOT NULL, output_path TEXT NOT NULL, description TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO resources VALUES (?, ?, ?, ?, ?)",
            ("resource-1", "u-legacy", str(artifact.resolve()), str(external), prose),
        )

    report = run_local_migration(tmp_path, "local-user", dry_run=False)

    with sqlite3.connect(canonical / "resources.db") as connection:
        row = connection.execute(
            "SELECT user_id, artifact_path, output_path, description FROM resources"
        ).fetchone()
    assert row == (
        "local-user",
        "code_runs/run-1/figure.png",
        str(external),
        prose,
    )
    assert report.unresolved_paths == (str(external),)
