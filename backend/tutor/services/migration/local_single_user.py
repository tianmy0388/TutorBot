"""Inventory and safely consolidate the two historical local data roots.

The migration is deliberately conservative: it creates a complete backup
before touching the canonical directory, copies only missing artifacts, and
uses stable SQLite keys with ``INSERT OR IGNORE`` when both roots contain the
same database. Source directories are never removed.
"""

from __future__ import annotations

import filecmp
import shutil
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

_SQLITE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})
_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-shm", "-wal")


@dataclass(frozen=True)
class MigrationReport:
    source_dirs: tuple[Path, ...]
    target_dir: Path
    backup_dir: Path | None
    discovered_users: tuple[str, ...]
    written_files: int


def build_migration_report(repo_root: Path, target_user_id: str) -> MigrationReport:
    """Inspect historical data roots without writing to either one."""
    _validate_target_user_id(target_user_id)
    root = Path(repo_root).resolve()
    candidates = (root / "data", root / "backend" / "data")
    sources = tuple(path.resolve() for path in candidates if path.is_dir())
    users = tuple(sorted(_discover_user_ids(sources)))
    return MigrationReport(sources, (root / "data").resolve(), None, users, 0)


def run_local_migration(
    repo_root: Path,
    target_user_id: str,
    dry_run: bool,
) -> MigrationReport:
    """Back up and consolidate local data, or return its read-only inventory."""
    report = build_migration_report(repo_root, target_user_id)
    if dry_run:
        return report

    backup_dir = _copy_sources_to_timestamped_backup(
        report.source_dirs,
        Path(repo_root).resolve() / "backups",
    )
    written = _merge_databases_and_artifacts(report, target_user_id)
    return replace(report, backup_dir=backup_dir, written_files=written)


def _validate_target_user_id(target_user_id: str) -> None:
    if not target_user_id.strip():
        raise ValueError("target_user_id must not be empty")


def _discover_user_ids(source_dirs: Iterable[Path]) -> set[str]:
    users: set[str] = set()
    for source_dir in source_dirs:
        for database in _iter_sqlite_files(source_dir):
            try:
                with _open_read_only(database) as connection:
                    for table in _user_id_tables(connection):
                        query = f"SELECT DISTINCT {_quote('user_id')} FROM {_quote(table)}"
                        for (user_id,) in connection.execute(query):
                            if user_id is not None and str(user_id).strip():
                                users.add(str(user_id))
            except sqlite3.Error:
                # A dry-run inventory must still report the other stores when
                # one stale or unrelated ``*.db`` file is not valid SQLite.
                continue
    return users


def _copy_sources_to_timestamped_backup(
    source_dirs: tuple[Path, ...],
    backup_root: Path,
) -> Path | None:
    if not source_dirs:
        return None

    backup_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = backup_root / f"local-data-{timestamp}"
    counter = 1
    while backup_dir.exists():
        backup_dir = backup_root / f"local-data-{timestamp}-{counter}"
        counter += 1
    backup_dir.mkdir()

    repo_root = backup_root.parent.resolve()
    for index, source in enumerate(source_dirs):
        try:
            relative_source = source.resolve().relative_to(repo_root)
        except ValueError:
            relative_source = Path(f"source-{index}") / source.name
        shutil.copytree(source, backup_dir / relative_source)
    return backup_dir.resolve()


def _merge_databases_and_artifacts(
    report: MigrationReport,
    target_user_id: str,
) -> int:
    if not report.source_dirs:
        return 0

    report.target_dir.mkdir(parents=True, exist_ok=True)
    written: set[Path] = set()

    for source_dir in report.source_dirs:
        for source_file in sorted(path for path in source_dir.rglob("*") if path.is_file()):
            relative_path = source_file.relative_to(source_dir)
            target_file = report.target_dir / relative_path
            if source_file.resolve() == target_file.resolve():
                continue
            if _is_sqlite_sidecar(source_file):
                continue

            target_file.parent.mkdir(parents=True, exist_ok=True)
            if _is_sqlite_file(source_file):
                if not target_file.exists():
                    shutil.copy2(source_file, target_file)
                    written.add(target_file)
                elif not filecmp.cmp(source_file, target_file, shallow=False):
                    if _merge_sqlite_database(source_file, target_file, target_user_id):
                        written.add(target_file)
                continue

            if not target_file.exists():
                shutil.copy2(source_file, target_file)
                written.add(target_file)
            elif filecmp.cmp(source_file, target_file, shallow=False):
                continue
            # Conflicting artifacts are retained at both source locations.
            # The canonical copy is not overwritten without a conflict field
            # in MigrationReport to make that loss visible.

    for database in _iter_sqlite_files(report.target_dir):
        if _rewrite_user_ids(database, target_user_id):
            written.add(database)
    return len(written)


def _merge_sqlite_database(source: Path, target: Path, target_user_id: str) -> bool:
    changed = False
    with _open_read_only(source) as source_connection, sqlite3.connect(target) as target_connection:
        target_connection.execute("PRAGMA foreign_keys = OFF")
        source_tables = source_connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()

        for table, create_sql in source_tables:
            target_table_exists = target_connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if target_table_exists is None:
                if not create_sql:
                    continue
                target_connection.execute(create_sql)

            source_columns = _table_columns(source_connection, table)
            target_columns = set(_table_columns(target_connection, table))
            columns = [column for column in source_columns if column in target_columns]
            if not columns:
                continue

            selected = ", ".join(_quote(column) for column in columns)
            placeholders = ", ".join("?" for _ in columns)
            insert = f"INSERT OR IGNORE INTO {_quote(table)} ({selected}) VALUES ({placeholders})"
            user_index = columns.index("user_id") if "user_id" in columns else None
            for row in source_connection.execute(f"SELECT {selected} FROM {_quote(table)}"):
                values = list(row)
                if user_index is not None:
                    values[user_index] = target_user_id
                cursor = target_connection.execute(insert, values)
                changed = changed or cursor.rowcount > 0

        target_connection.commit()
    return changed


def _rewrite_user_ids(database: Path, target_user_id: str) -> bool:
    changed = False
    try:
        with sqlite3.connect(database) as connection:
            for table in _user_id_tables(connection):
                cursor = connection.execute(
                    f"UPDATE OR IGNORE {_quote(table)} "
                    f"SET {_quote('user_id')} = ? "
                    f"WHERE {_quote('user_id')} IS NOT NULL "
                    f"AND {_quote('user_id')} != ?",
                    (target_user_id, target_user_id),
                )
                changed = changed or cursor.rowcount > 0
            connection.commit()
    except sqlite3.Error:
        return False
    return changed


def _iter_sqlite_files(source_dir: Path) -> Iterable[Path]:
    if not source_dir.is_dir():
        return ()
    return (path for path in sorted(source_dir.rglob("*")) if path.is_file() and _is_sqlite_file(path))


def _is_sqlite_file(path: Path) -> bool:
    return path.suffix.lower() in _SQLITE_SUFFIXES


def _is_sqlite_sidecar(path: Path) -> bool:
    return path.name.lower().endswith(_SQLITE_SIDECAR_SUFFIXES)


def _open_read_only(database: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)


def _user_id_tables(connection: sqlite3.Connection) -> tuple[str, ...]:
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return tuple(table for (table,) in tables if "user_id" in _table_columns(connection, table))


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote(table)})")]


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


__all__ = ["MigrationReport", "build_migration_report", "run_local_migration"]
