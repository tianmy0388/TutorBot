"""Inventory and safely consolidate the two historical local data roots.

The migration is deliberately conservative: it creates a complete backup
before touching the canonical directory, copies only missing artifacts, and
uses stable SQLite keys with ``INSERT OR IGNORE`` when both roots contain the
same database. Source directories are never removed.
"""

from __future__ import annotations

import filecmp
import json
import shutil
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from tutor.services.identity import LOCAL_USER_ID

_SQLITE_SUFFIXES = frozenset({".db", ".sqlite", ".sqlite3"})
_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-shm", "-wal")
_OWNERSHIP_COLUMNS = ("user_id", "owner_user_id")
_ARTIFACT_PATH_COLUMNS = frozenset(
    {
        "artifact_key",
        "artifact_path",
        "file_path",
        "image_path",
        "output_path",
        "path",
        "public_path",
        "video_path",
    }
)


@dataclass(frozen=True)
class MigrationReport:
    source_dirs: tuple[Path, ...]
    target_dir: Path
    backup_dir: Path | None
    discovered_users: tuple[str, ...]
    written_files: int
    unresolved_paths: tuple[str, ...] = ()


class MigrationError(RuntimeError):
    """Raised when a migration cannot preserve its ownership guarantees."""


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
    written, unresolved_paths = _merge_databases_and_artifacts(report, target_user_id)
    return replace(
        report,
        backup_dir=backup_dir,
        written_files=written,
        unresolved_paths=unresolved_paths,
    )


def _validate_target_user_id(target_user_id: str) -> None:
    if target_user_id != LOCAL_USER_ID:
        raise ValueError("--target-user-id must be exactly 'local-user'")


def _discover_user_ids(source_dirs: Iterable[Path]) -> set[str]:
    users: set[str] = set()
    for source_dir in source_dirs:
        for database in _iter_sqlite_files(source_dir):
            try:
                with _open_read_only(database) as connection:
                    for table, columns in _ownership_tables(connection):
                        for ownership_column in columns:
                            query = f"SELECT DISTINCT {_quote(ownership_column)} FROM {_quote(table)}"
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
        backup_source = backup_dir / relative_source
        shutil.copytree(source, backup_source, ignore=_ignore_sqlite_sidecars)
        for database in _iter_sqlite_files(source):
            _copy_sqlite_database(database, backup_source / database.relative_to(source))
    return backup_dir.resolve()


def _merge_databases_and_artifacts(
    report: MigrationReport,
    target_user_id: str,
) -> tuple[int, tuple[str, ...]]:
    if not report.source_dirs:
        return 0, ()

    report.target_dir.mkdir(parents=True, exist_ok=True)
    written: set[Path] = set()
    unresolved_paths: set[str] = set()

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
                    _copy_sqlite_database(source_file, target_file)
                    written.add(target_file)
                elif _merge_sqlite_database(source_file, target_file, target_user_id):
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
        if _normalize_artifact_paths(
            database,
            (*report.source_dirs, report.target_dir),
            unresolved_paths,
        ):
            written.add(database)
    return len(written), tuple(sorted(unresolved_paths))


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
            ownership_columns = [column for column in _OWNERSHIP_COLUMNS if column in columns]
            insert = f"INSERT INTO {_quote(table)} ({selected}) VALUES ({placeholders})"
            if ownership_columns:
                assignments = ", ".join(
                    f"{_quote(column)} = excluded.{_quote(column)}" for column in ownership_columns
                )
                ownership_changed = " OR ".join(
                    f"{_quote(column)} IS NOT excluded.{_quote(column)}" for column in ownership_columns
                )
                insert += f" ON CONFLICT DO UPDATE SET {assignments} WHERE {ownership_changed}"
            else:
                insert += " ON CONFLICT DO NOTHING"
            ownership_indexes = [columns.index(column) for column in ownership_columns]
            for row in source_connection.execute(f"SELECT {selected} FROM {_quote(table)}"):
                values = list(row)
                for ownership_index in ownership_indexes:
                    if values[ownership_index] is not None:
                        values[ownership_index] = target_user_id
                cursor = target_connection.execute(insert, values)
                changed = changed or cursor.rowcount > 0

        target_connection.commit()
    return changed


def _rewrite_user_ids(database: Path, target_user_id: str) -> bool:
    changed = False
    try:
        with sqlite3.connect(database) as connection:
            for table, ownership_columns in _ownership_tables(connection):
                needs_rewrite = " OR ".join(
                    f"({_quote(column)} IS NOT NULL AND {_quote(column)} != ?)"
                    for column in ownership_columns
                )
                selected_ownership = ", ".join(_quote(column) for column in ownership_columns)
                legacy_rows = connection.execute(
                    f"SELECT rowid, {selected_ownership} FROM {_quote(table)} "
                    f"WHERE {needs_rewrite} ORDER BY rowid",
                    (target_user_id,) * len(ownership_columns),
                ).fetchall()
                for rowid, *ownership_values in legacy_rows:
                    columns_to_rewrite = [
                        column
                        for column, value in zip(ownership_columns, ownership_values, strict=True)
                        if value is not None and value != target_user_id
                    ]
                    assignments = ", ".join(f"{_quote(column)} = ?" for column in columns_to_rewrite)
                    try:
                        cursor = connection.execute(
                            f"UPDATE {_quote(table)} SET {assignments} WHERE rowid = ?",
                            (target_user_id,) * len(columns_to_rewrite) + (rowid,),
                        )
                    except sqlite3.IntegrityError:
                        # A canonical row already owns the same unique business
                        # key. Keep that row and discard this migrated duplicate;
                        # the pre-write backup retains the original source row.
                        cursor = connection.execute(
                            f"DELETE FROM {_quote(table)} WHERE rowid = ?",
                            (rowid,),
                        )
                    changed = changed or cursor.rowcount > 0

                remaining = connection.execute(
                    f"SELECT COUNT(*) FROM {_quote(table)} WHERE {needs_rewrite}",
                    (target_user_id,) * len(ownership_columns),
                ).fetchone()[0]
                if remaining:
                    raise MigrationError(f"could not rewrite all user IDs in {database}:{table}")
    except sqlite3.Error as exc:
        raise MigrationError(f"could not rewrite user IDs in {database}: {exc}") from exc
    return changed


def _normalize_artifact_paths(
    database: Path,
    data_roots: tuple[Path, ...],
    unresolved_paths: set[str],
) -> bool:
    changed = False
    try:
        with sqlite3.connect(database) as connection:
            tables = connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            for (table,) in tables:
                column_info = connection.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
                for column in column_info:
                    column_name = str(column[1])
                    declared_type = str(column[2]).upper()
                    if column_name.lower() in _ARTIFACT_PATH_COLUMNS:
                        changed = (
                            _normalize_path_column(
                                connection,
                                table,
                                column_name,
                                data_roots,
                                unresolved_paths,
                            )
                            or changed
                        )
                    elif declared_type == "JSON":
                        changed = (
                            _normalize_json_column(
                                connection,
                                table,
                                column_name,
                                data_roots,
                                unresolved_paths,
                            )
                            or changed
                        )
    except sqlite3.Error as exc:
        raise MigrationError(f"could not normalize artifact paths in {database}: {exc}") from exc
    return changed


def _normalize_path_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    data_roots: tuple[Path, ...],
    unresolved_paths: set[str],
) -> bool:
    changed = False
    rows = connection.execute(
        f"SELECT rowid, {_quote(column)} FROM {_quote(table)} WHERE {_quote(column)} IS NOT NULL"
    ).fetchall()
    for rowid, value in rows:
        normalized = _normalize_path_value(value, data_roots, unresolved_paths)
        if normalized != value:
            connection.execute(
                f"UPDATE {_quote(table)} SET {_quote(column)} = ? WHERE rowid = ?",
                (normalized, rowid),
            )
            changed = True
    return changed


def _normalize_json_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    data_roots: tuple[Path, ...],
    unresolved_paths: set[str],
) -> bool:
    changed = False
    rows = connection.execute(
        f"SELECT rowid, {_quote(column)} FROM {_quote(table)} WHERE {_quote(column)} IS NOT NULL"
    ).fetchall()
    for rowid, value in rows:
        if not isinstance(value, str):
            continue
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            continue
        normalized = _normalize_json_value(payload, data_roots, unresolved_paths)
        if normalized != payload:
            connection.execute(
                f"UPDATE {_quote(table)} SET {_quote(column)} = ? WHERE rowid = ?",
                (json.dumps(normalized, ensure_ascii=False, separators=(",", ":")), rowid),
            )
            changed = True
    return changed


def _normalize_json_value(
    value: object,
    data_roots: tuple[Path, ...],
    unresolved_paths: set[str],
) -> object:
    if isinstance(value, dict):
        return {
            key: (
                _normalize_path_value(item, data_roots, unresolved_paths)
                if str(key).lower() in _ARTIFACT_PATH_COLUMNS
                else _normalize_json_value(item, data_roots, unresolved_paths)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalize_json_value(item, data_roots, unresolved_paths) for item in value]
    return value


def _normalize_path_value(
    value: object,
    data_roots: tuple[Path, ...],
    unresolved_paths: set[str],
) -> object:
    if not isinstance(value, str) or not value:
        return value
    path = Path(value)
    if not path.is_absolute():
        return value.replace("\\", "/")

    resolved = path.resolve()
    for data_root in data_roots:
        try:
            return resolved.relative_to(data_root.resolve()).as_posix()
        except ValueError:
            continue
    unresolved_paths.add(value)
    return value


def _iter_sqlite_files(source_dir: Path) -> Iterable[Path]:
    if not source_dir.is_dir():
        return ()
    return (path for path in sorted(source_dir.rglob("*")) if path.is_file() and _is_sqlite_file(path))


def _is_sqlite_file(path: Path) -> bool:
    return path.suffix.lower() in _SQLITE_SUFFIXES


def _is_sqlite_sidecar(path: Path) -> bool:
    return path.name.lower().endswith(_SQLITE_SIDECAR_SUFFIXES)


def _ignore_sqlite_sidecars(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name.lower().endswith(_SQLITE_SIDECAR_SUFFIXES)}


def _copy_sqlite_database(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with _open_read_only(source) as source_connection, sqlite3.connect(target) as target_connection:
        source_connection.backup(target_connection)


def _open_read_only(database: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)


def _ownership_tables(
    connection: sqlite3.Connection,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    tables = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    ownership_tables: list[tuple[str, tuple[str, ...]]] = []
    for (table,) in tables:
        table_columns = set(_table_columns(connection, table))
        ownership_columns = tuple(column for column in _OWNERSHIP_COLUMNS if column in table_columns)
        if ownership_columns:
            ownership_tables.append((table, ownership_columns))
    return tuple(ownership_tables)


def _table_columns(connection: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in connection.execute(f"PRAGMA table_info({_quote(table)})")]


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


__all__ = [
    "MigrationError",
    "MigrationReport",
    "build_migration_report",
    "run_local_migration",
]
