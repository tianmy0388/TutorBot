from __future__ import annotations

import json
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


def test_migration_rewrites_nested_json_owners_and_keeps_newest_profile(tmp_path):
    from tutor.services.migration.local_single_user import run_local_migration

    canonical = tmp_path / "data"
    canonical.mkdir(exist_ok=True)
    database = canonical / "profiles.db"
    older = {
        "user_id": "local-user",
        "version": 1,
        "event_watermark": 0,
        "metadata": {"owner_user_id": "local-user"},
    }
    newer = {
        "user_id": "legacy-user",
        "version": 4,
        "event_watermark": 12,
        "metadata": {
            "owner_user_id": "legacy-user",
            "history": [{"user_id": "another-legacy-user"}],
        },
    }
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE profiles ("
            "user_id TEXT PRIMARY KEY, version INTEGER NOT NULL, "
            "profile_data JSON NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO profiles VALUES (?, ?, ?, ?, ?)",
            (
                ("local-user", 1, json.dumps(older), "2026-01-01", "2026-01-01"),
                ("legacy-user", 4, json.dumps(newer), "2026-01-02", "2026-01-04"),
            ),
        )

    run_local_migration(tmp_path, "local-user", dry_run=False)

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT user_id, version, profile_data, updated_at FROM profiles"
        ).fetchall()
    assert len(rows) == 1
    user_id, version, profile_data, updated_at = rows[0]
    assert (user_id, version, updated_at) == ("local-user", 4, "2026-01-04")
    payload = json.loads(profile_data)
    assert payload["user_id"] == "local-user"
    assert payload["metadata"]["owner_user_id"] == "local-user"
    assert payload["metadata"]["history"][0]["user_id"] == "local-user"


def test_migration_keeps_newest_profile_when_collision_spans_data_roots(tmp_path):
    from tutor.services.migration.local_single_user import run_local_migration

    canonical = tmp_path / "data"
    legacy = tmp_path / "backend" / "data"
    canonical.mkdir(exist_ok=True)
    legacy.mkdir(parents=True)
    schema = (
        "CREATE TABLE profiles ("
        "user_id TEXT PRIMARY KEY, version INTEGER NOT NULL, "
        "profile_data JSON NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    older = {
        "user_id": "local-user",
        "version": 1,
        "event_watermark": 2,
    }
    newer = {
        "user_id": "legacy-user",
        "version": 4,
        "event_watermark": 12,
        "metadata": {"owner_user_id": "legacy-user"},
    }
    with sqlite3.connect(canonical / "profiles.db") as connection:
        connection.execute(schema)
        connection.execute(
            "INSERT INTO profiles VALUES (?, ?, ?, ?, ?)",
            ("local-user", 1, json.dumps(older), "2026-01-01", "2026-01-02"),
        )
    with sqlite3.connect(legacy / "profiles.db") as connection:
        connection.execute(schema)
        connection.execute(
            "INSERT INTO profiles VALUES (?, ?, ?, ?, ?)",
            ("legacy-user", 4, json.dumps(newer), "2026-01-03", "2026-01-04"),
        )

    run_local_migration(tmp_path, "local-user", dry_run=False)

    with sqlite3.connect(canonical / "profiles.db") as connection:
        row = connection.execute(
            "SELECT user_id, version, profile_data, updated_at FROM profiles"
        ).fetchone()
    assert row is not None
    user_id, version, profile_data, updated_at = row
    assert (user_id, version, updated_at) == ("local-user", 4, "2026-01-04")
    payload = json.loads(profile_data)
    assert payload["user_id"] == "local-user"
    assert payload["metadata"]["owner_user_id"] == "local-user"
    with sqlite3.connect(legacy / "profiles.db") as connection:
        assert connection.execute(
            "SELECT user_id, version FROM profiles"
        ).fetchone() == ("legacy-user", 4)


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


def test_cli_accepts_explicit_former_repo_root_for_path_relocation(tmp_path):
    from tutor.cli.main import app

    repo = tmp_path / "TutorBot"
    old_repo = tmp_path / "Tutor"
    (repo / "data").mkdir(parents=True)

    result = CliRunner().invoke(
        app,
        [
            "migrate-local-data",
            "--repo-root",
            str(repo),
            "--target-user-id",
            "local-user",
            "--relocate-from",
            str(old_repo),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "relocate_from:" in result.output


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


def test_migration_recovers_artifact_path_from_a_renamed_repo_root(tmp_path):
    from tutor.services.migration.local_single_user import run_local_migration

    canonical = tmp_path / "TutorBot" / "data"
    legacy = tmp_path / "TutorBot" / "backend" / "data"
    canonical.mkdir(parents=True)
    legacy.mkdir(parents=True)
    artifact = legacy / "code_runs" / "run-1" / "figure.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"png")
    stale_repo = tmp_path / "Tutor"
    stale_artifact = (
        stale_repo / "backend" / "data" / "code_runs" / "run-1" / "figure.png"
    ).resolve()

    with sqlite3.connect(legacy / "resources.db") as connection:
        connection.execute(
            "CREATE TABLE resources ("
            "resource_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, artifact_path TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO resources VALUES (?, ?, ?)",
            ("resource-1", "u-legacy", str(stale_artifact)),
        )

    report = run_local_migration(
        tmp_path / "TutorBot",
        "local-user",
        dry_run=False,
        relocate_from=(stale_repo,),
    )

    with sqlite3.connect(canonical / "resources.db") as connection:
        artifact_path = connection.execute("SELECT artifact_path FROM resources").fetchone()[0]
    assert artifact_path == "code_runs/run-1/figure.png"
    assert report.unresolved_paths == ()


@pytest.mark.parametrize("allowlisted", [False, True])
def test_migration_never_retargets_an_existing_external_artifact(tmp_path, allowlisted):
    from tutor.services.migration.local_single_user import run_local_migration

    repo = tmp_path / "TutorBot"
    canonical = repo / "data"
    legacy = repo / "backend" / "data"
    canonical_artifact = canonical / "code_runs" / "run-1" / "figure.png"
    canonical_artifact.parent.mkdir(parents=True)
    canonical_artifact.write_bytes(b"canonical")
    legacy.mkdir(parents=True)

    external_repo = tmp_path / "archive"
    external_artifact = external_repo / "data" / "code_runs" / "run-1" / "figure.png"
    external_artifact.parent.mkdir(parents=True)
    external_artifact.write_bytes(b"external")
    with sqlite3.connect(legacy / "resources.db") as connection:
        connection.execute(
            "CREATE TABLE resources ("
            "resource_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, artifact_path TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO resources VALUES (?, ?, ?)",
            ("resource-1", "u-legacy", str(external_artifact.resolve())),
        )

    report = run_local_migration(
        repo,
        "local-user",
        dry_run=False,
        relocate_from=(external_repo,) if allowlisted else (),
    )

    with sqlite3.connect(canonical / "resources.db") as connection:
        artifact_path = connection.execute("SELECT artifact_path FROM resources").fetchone()[0]
    assert artifact_path == str(external_artifact.resolve())
    assert report.unresolved_paths == (str(external_artifact.resolve()),)


def test_source_user_filter_keeps_only_selected_owner_and_related_artifacts(tmp_path):
    from tutor.services.migration.local_single_user import run_local_migration

    legacy = tmp_path / "backend" / "data"
    legacy.mkdir(parents=True)
    with sqlite3.connect(legacy / "conversations.db") as connection:
        connection.execute(
            "CREATE TABLE conversations (session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE messages (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, content TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO conversations VALUES (?, ?)",
            (("real-session", "u-real"), ("demo-session", "demo-user")),
        )
        connection.executemany(
            "INSERT INTO messages VALUES (?, ?, ?)",
            (("real-message", "real-session", "keep"), ("demo-message", "demo-session", "drop")),
        )
    with sqlite3.connect(legacy / "resources.db") as connection:
        connection.execute(
            "CREATE TABLE resources (resource_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, format_specific JSON NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO resources VALUES (?, ?, ?)",
            (
                ("real-resource", "u-real", json.dumps({"artifact_key": "code_runs/real/output.png"})),
                ("demo-resource", "demo-user", json.dumps({"artifact_key": "code_runs/demo/output.png"})),
            ),
        )
    real_artifact = legacy / "code_runs" / "real" / "output.png"
    demo_artifact = legacy / "code_runs" / "demo" / "output.png"
    real_artifact.parent.mkdir(parents=True)
    demo_artifact.parent.mkdir(parents=True)
    real_artifact.write_bytes(b"real")
    demo_artifact.write_bytes(b"demo")

    report = run_local_migration(
        tmp_path,
        "local-user",
        dry_run=False,
        source_user_id="u-real",
    )

    with sqlite3.connect(tmp_path / "data" / "conversations.db") as connection:
        assert connection.execute("SELECT session_id, user_id FROM conversations").fetchall() == [
            ("real-session", "local-user")
        ]
        assert connection.execute("SELECT id, session_id FROM messages").fetchall() == [
            ("real-message", "real-session")
        ]
    with sqlite3.connect(tmp_path / "data" / "resources.db") as connection:
        assert connection.execute("SELECT resource_id, user_id FROM resources").fetchall() == [
            ("real-resource", "local-user")
        ]
    assert (tmp_path / "data" / "code_runs" / "real" / "output.png").read_bytes() == b"real"
    assert not (tmp_path / "data" / "code_runs" / "demo" / "output.png").exists()
    assert report.source_user_id == "u-real"
    assert report.backup_dir is not None
    assert (report.backup_dir / "backend" / "data" / "code_runs" / "demo" / "output.png").exists()


def test_cli_accepts_source_user_filter(tmp_path):
    from tutor.cli.main import app

    data_dir = tmp_path / "backend" / "data"
    data_dir.mkdir(parents=True)
    with sqlite3.connect(data_dir / "profiles.db") as connection:
        connection.execute("CREATE TABLE profiles (user_id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO profiles VALUES ('u-real')")

    result = CliRunner().invoke(
        app,
        [
            "migrate-local-data",
            "--repo-root",
            str(tmp_path),
            "--source-user-id",
            "u-real",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "selected_user: u-real" in result.output
