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
_GLOBAL_METADATA_TABLES = frozenset({"alembic_version", "schema_meta"})
_GLOBAL_ARTIFACT_ROOTS = frozenset({"knowledge_bases"})
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
    source_user_id: str | None = None
    unresolved_paths: tuple[str, ...] = ()
    relocation_roots: tuple[Path, ...] = ()


class MigrationError(RuntimeError):
    """Raised when a migration cannot preserve its ownership guarantees."""


def build_migration_report(
    repo_root: Path,
    target_user_id: str,
    *,
    source_user_id: str | None = None,
    relocate_from: Iterable[Path] = (),
) -> MigrationReport:
    """Inspect historical data roots without writing to either one."""
    _validate_target_user_id(target_user_id)
    root = Path(repo_root).resolve()
    candidates = (root / "data", root / "backend" / "data")
    sources = tuple(path.resolve() for path in candidates if path.is_dir())
    users = tuple(sorted(_discover_user_ids(sources)))
    if source_user_id is not None:
        source_user_id = source_user_id.strip()
        if not source_user_id:
            raise ValueError("--source-user-id must not be empty")
        if source_user_id not in users:
            raise ValueError(f"--source-user-id was not found: {source_user_id}")
    relocation_roots = tuple(Path(path).resolve() for path in relocate_from)
    return MigrationReport(
        sources,
        (root / "data").resolve(),
        None,
        users,
        0,
        source_user_id=source_user_id,
        relocation_roots=relocation_roots,
    )


def run_local_migration(
    repo_root: Path,
    target_user_id: str,
    dry_run: bool,
    *,
    source_user_id: str | None = None,
    relocate_from: Iterable[Path] = (),
) -> MigrationReport:
    """Back up and consolidate local data, or return its read-only inventory."""
    report = build_migration_report(
        repo_root,
        target_user_id,
        source_user_id=source_user_id,
        relocate_from=relocate_from,
    )
    if dry_run:
        return report

    backup_dir = _copy_sources_to_timestamped_backup(
        report.source_dirs,
        Path(repo_root).resolve() / "backups",
    )
    written, unresolved_paths = _merge_databases_and_artifacts(
        report,
        target_user_id,
        source_user_id=source_user_id,
    )
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
    *,
    source_user_id: str | None,
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
                if not target_file.exists() and source_user_id is None:
                    _copy_sqlite_database(source_file, target_file)
                    written.add(target_file)
                elif _merge_sqlite_database(
                    source_file,
                    target_file,
                    target_user_id,
                    source_user_id=source_user_id,
                ):
                    written.add(target_file)
                continue

            if source_user_id is not None:
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
        if source_user_id is not None and _filter_database_for_user(
            database,
            source_user_id,
            target_user_id,
        ):
            written.add(database)
        if _rewrite_user_ids(
            database,
            target_user_id,
            source_user_id=source_user_id,
        ):
            written.add(database)
        if _normalize_artifact_paths(
            database,
            (*report.source_dirs, report.target_dir),
            unresolved_paths,
            report.relocation_roots,
            target_user_id,
        ):
            written.add(database)
    if source_user_id is not None:
        written.update(_copy_selected_artifacts(report))
    return len(written), tuple(sorted(unresolved_paths))


def _merge_sqlite_database(
    source: Path,
    target: Path,
    target_user_id: str,
    *,
    source_user_id: str | None = None,
) -> bool:
    changed = False
    with _open_read_only(source) as source_connection, sqlite3.connect(target) as target_connection:
        target_connection.execute("PRAGMA foreign_keys = OFF")
        source_tables = source_connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        source_scope = _selected_relation_values(source_connection, source_user_id)
        source_has_owned_tables = bool(_ownership_tables(source_connection))

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
            select_sql = f"SELECT {selected} FROM {_quote(table)}"
            select_params: tuple[object, ...] = ()
            if source_user_id is not None:
                selection = _table_source_filter(
                    table,
                    columns,
                    source_scope,
                    source_has_owned_tables,
                    source_user_id,
                )
                if selection is None:
                    continue
                where_sql, select_params = selection
                if where_sql:
                    select_sql += f" WHERE {where_sql}"
            for row in source_connection.execute(select_sql, select_params):
                values = list(row)
                for ownership_index in ownership_indexes:
                    if values[ownership_index] is not None:
                        values[ownership_index] = target_user_id
                profile_changed = _merge_profile_snapshot(
                    target_connection,
                    table,
                    columns,
                    values,
                    target_user_id,
                )
                if profile_changed is not None:
                    changed = changed or profile_changed
                    continue
                cursor = target_connection.execute(insert, values)
                changed = changed or cursor.rowcount > 0

        target_connection.commit()
    return changed


def _merge_profile_snapshot(
    connection: sqlite3.Connection,
    table: str,
    columns: list[str],
    values: list[object],
    target_user_id: str,
) -> bool | None:
    """Merge a cross-root profile collision without discarding newer state."""
    required = {"user_id", "version", "profile_data", "updated_at"}
    if table != "profiles" or not required.issubset(columns):
        return None

    indexes = {column: columns.index(column) for column in required}
    existing = connection.execute(
        f"SELECT {_quote('version')}, {_quote('profile_data')}, {_quote('updated_at')} "
        f"FROM {_quote(table)} WHERE {_quote('user_id')} = ?",
        (target_user_id,),
    ).fetchone()
    if existing is None:
        selected = ", ".join(_quote(column) for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        cursor = connection.execute(
            f"INSERT INTO {_quote(table)} ({selected}) VALUES ({placeholders})",
            values,
        )
        return cursor.rowcount > 0

    candidate_rank = _profile_snapshot_rank(
        values[indexes["version"]],
        values[indexes["profile_data"]],
        values[indexes["updated_at"]],
    )
    if candidate_rank <= _profile_snapshot_rank(*existing):
        return False

    snapshot_columns = [column for column in columns if column != "user_id"]
    assignments = ", ".join(f"{_quote(column)} = ?" for column in snapshot_columns)
    cursor = connection.execute(
        f"UPDATE {_quote(table)} SET {assignments} WHERE {_quote('user_id')} = ?",
        (
            *(values[columns.index(column)] for column in snapshot_columns),
            target_user_id,
        ),
    )
    return cursor.rowcount > 0


def _selected_relation_values(
    connection: sqlite3.Connection,
    source_user_id: str | None,
) -> dict[str, set[object]]:
    values: dict[str, set[object]] = {}
    if source_user_id is None:
        return values
    for table, ownership_columns in _ownership_tables(connection):
        columns = _table_columns(connection, table)
        identifiers = [
            column
            for column in columns
            if column not in _OWNERSHIP_COLUMNS
            and (column == "id" or column.endswith("_id"))
        ]
        if not identifiers:
            continue
        selected = ", ".join(_quote(column) for column in identifiers)
        owner_filter = " OR ".join(
            f"{_quote(column)} = ?" for column in ownership_columns
        )
        for row in connection.execute(
            f"SELECT {selected} FROM {_quote(table)} WHERE {owner_filter}",
            (source_user_id,) * len(ownership_columns),
        ):
            for column, item in zip(identifiers, row, strict=True):
                if item is not None and str(item).strip():
                    values.setdefault(column, set()).add(item)
    return values


def _table_source_filter(
    table: str,
    columns: list[str],
    relation_values: dict[str, set[object]],
    database_has_owned_tables: bool,
    source_user_id: str,
) -> tuple[str, tuple[object, ...]] | None:
    ownership_columns = [column for column in _OWNERSHIP_COLUMNS if column in columns]
    if ownership_columns:
        return (
            " OR ".join(f"{_quote(column)} = ?" for column in ownership_columns),
            (source_user_id,) * len(ownership_columns),
        )
    if not database_has_owned_tables or table in _GLOBAL_METADATA_TABLES:
        return "", ()
    predicates: list[str] = []
    params: list[object] = []
    for column in columns:
        selected = relation_values.get(column)
        if not selected:
            continue
        ordered = sorted(selected, key=str)
        predicates.append(
            f"{_quote(column)} IN ({', '.join('?' for _ in ordered)})"
        )
        params.extend(ordered)
    if not predicates:
        return None
    return " OR ".join(predicates), tuple(params)


def _filter_database_for_user(
    database: Path,
    source_user_id: str,
    target_user_id: str,
) -> bool:
    changed = False
    try:
        with sqlite3.connect(database) as connection:
            ownership_tables = _ownership_tables(connection)
            if not ownership_tables:
                return False
            source_values = _selected_relation_values(connection, source_user_id)
            target_values = _selected_relation_values(connection, target_user_id)
            relation_values = {
                column: source_values.get(column, set()) | target_values.get(column, set())
                for column in set(source_values) | set(target_values)
            }
            owned_names = {table for table, _columns in ownership_tables}
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for table in tables:
                if table in _GLOBAL_METADATA_TABLES:
                    continue
                columns = _table_columns(connection, table)
                if table in owned_names:
                    ownership_columns = [
                        column for column in _OWNERSHIP_COLUMNS if column in columns
                    ]
                    keep = " OR ".join(
                        f"COALESCE({_quote(column)} IN (?, ?), 0)"
                        for column in ownership_columns
                    )
                    cursor = connection.execute(
                        f"DELETE FROM {_quote(table)} WHERE NOT ({keep})",
                        tuple(
                            item
                            for _column in ownership_columns
                            for item in (source_user_id, target_user_id)
                        ),
                    )
                    changed = changed or cursor.rowcount > 0
                    continue
                selection = _table_source_filter(
                    table,
                    columns,
                    relation_values,
                    True,
                    source_user_id,
                )
                if selection is None:
                    cursor = connection.execute(f"DELETE FROM {_quote(table)}")
                else:
                    where_sql, params = selection
                    if not where_sql:
                        continue
                    cursor = connection.execute(
                        f"DELETE FROM {_quote(table)} WHERE NOT ({where_sql})",
                        params,
                    )
                changed = changed or cursor.rowcount > 0
    except sqlite3.Error as exc:
        raise MigrationError(f"could not filter users in {database}: {exc}") from exc
    return changed


def _rewrite_user_ids(
    database: Path,
    target_user_id: str,
    *,
    source_user_id: str | None = None,
) -> bool:
    changed = False
    try:
        with sqlite3.connect(database) as connection:
            for table, ownership_columns in _ownership_tables(connection):
                if source_user_id is None:
                    needs_rewrite = " OR ".join(
                        f"({_quote(column)} IS NOT NULL AND {_quote(column)} != ?)"
                        for column in ownership_columns
                    )
                    rewrite_params = (target_user_id,) * len(ownership_columns)
                else:
                    needs_rewrite = " OR ".join(
                        f"{_quote(column)} = ?" for column in ownership_columns
                    )
                    rewrite_params = (source_user_id,) * len(ownership_columns)
                selected_ownership = ", ".join(_quote(column) for column in ownership_columns)
                legacy_rows = connection.execute(
                    f"SELECT rowid, {selected_ownership} FROM {_quote(table)} "
                    f"WHERE {needs_rewrite} ORDER BY rowid",
                    rewrite_params,
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
                        # key. Profiles are state snapshots, so preserve the
                        # newest snapshot before collapsing the duplicate.
                        _preserve_newest_profile_collision(
                            connection,
                            table,
                            rowid,
                            target_user_id,
                            ownership_columns,
                        )
                        # Other tables keep the canonical row; the pre-write
                        # backup retains every original source row.
                        cursor = connection.execute(
                            f"DELETE FROM {_quote(table)} WHERE rowid = ?",
                            (rowid,),
                        )
                    changed = changed or cursor.rowcount > 0

                remaining = connection.execute(
                    f"SELECT COUNT(*) FROM {_quote(table)} WHERE {needs_rewrite}",
                    rewrite_params,
                ).fetchone()[0]
                if remaining:
                    raise MigrationError(f"could not rewrite all user IDs in {database}:{table}")
    except sqlite3.Error as exc:
        raise MigrationError(f"could not rewrite user IDs in {database}: {exc}") from exc
    return changed


def _copy_selected_artifacts(report: MigrationReport) -> set[Path]:
    references = _collect_artifact_references(report.target_dir)
    references.update(_GLOBAL_ARTIFACT_ROOTS)
    written: set[Path] = set()
    for raw_reference in sorted(references):
        reference = Path(raw_reference.replace("\\", "/"))
        if reference.is_absolute() or ".." in reference.parts or not reference.parts:
            continue
        for source_dir in report.source_dirs:
            source = source_dir / reference
            target = report.target_dir / reference
            if source.resolve() == target.resolve() or not source.exists():
                continue
            if source.is_dir():
                before = target.exists()
                shutil.copytree(source, target, dirs_exist_ok=True)
                if not before:
                    written.add(target)
            elif not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                written.add(target)
            break
    return written


def _collect_artifact_references(data_dir: Path) -> set[str]:
    references: set[str] = set()
    for database in _iter_sqlite_files(data_dir):
        try:
            with _open_read_only(database) as connection:
                tables = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                for (table,) in tables:
                    for column in connection.execute(
                        f"PRAGMA table_info({_quote(table)})"
                    ):
                        column_name = str(column[1])
                        declared_type = str(column[2]).upper()
                        rows = connection.execute(
                            f"SELECT {_quote(column_name)} FROM {_quote(table)} "
                            f"WHERE {_quote(column_name)} IS NOT NULL"
                        )
                        if column_name.lower() in _ARTIFACT_PATH_COLUMNS:
                            references.update(
                                str(value)
                                for (value,) in rows
                                if isinstance(value, str) and value.strip()
                            )
                        elif declared_type == "JSON":
                            for (value,) in rows:
                                if not isinstance(value, str):
                                    payload = value
                                else:
                                    try:
                                        payload = json.loads(value)
                                    except json.JSONDecodeError:
                                        continue
                                _collect_json_artifact_references(payload, references)
        except sqlite3.Error:
            continue
    return references


def _collect_json_artifact_references(value: object, references: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if (
                str(key).lower() in _ARTIFACT_PATH_COLUMNS
                and isinstance(item, str)
                and item.strip()
            ):
                references.add(item)
            else:
                _collect_json_artifact_references(item, references)
    elif isinstance(value, list):
        for item in value:
            _collect_json_artifact_references(item, references)


def _preserve_newest_profile_collision(
    connection: sqlite3.Connection,
    table: str,
    legacy_rowid: int,
    target_user_id: str,
    ownership_columns: tuple[str, ...],
) -> bool:
    """Keep the newest profile snapshot when owner normalisation collides."""
    if table != "profiles" or ownership_columns != ("user_id",):
        return False
    columns = set(_table_columns(connection, table))
    snapshot_columns = ("version", "profile_data", "created_at", "updated_at")
    if not {"user_id", *snapshot_columns}.issubset(columns):
        return False
    selected = ", ".join(_quote(column) for column in snapshot_columns)
    legacy = connection.execute(
        f"SELECT {selected} FROM {_quote(table)} WHERE rowid = ?",
        (legacy_rowid,),
    ).fetchone()
    canonical = connection.execute(
        f"SELECT {selected} FROM {_quote(table)} WHERE {_quote('user_id')} = ?",
        (target_user_id,),
    ).fetchone()
    if legacy is None or canonical is None:
        return False
    legacy_rank = _profile_snapshot_rank(legacy[0], legacy[1], legacy[3])
    canonical_rank = _profile_snapshot_rank(canonical[0], canonical[1], canonical[3])
    if legacy_rank <= canonical_rank:
        return False
    assignments = ", ".join(f"{_quote(column)} = ?" for column in snapshot_columns)
    connection.execute(
        f"UPDATE {_quote(table)} SET {assignments} WHERE {_quote('user_id')} = ?",
        (*legacy, target_user_id),
    )
    return True


def _profile_snapshot_rank(
    version: object,
    profile_data: object,
    updated_at: object,
) -> tuple[int, int, str]:
    try:
        payload = json.loads(str(profile_data or "{}"))
    except (json.JSONDecodeError, TypeError, ValueError):
        payload = {}

    def as_int(value: object) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    return (
        as_int(payload.get("event_watermark")),
        as_int(version),
        str(updated_at or ""),
    )


def _normalize_artifact_paths(
    database: Path,
    data_roots: tuple[Path, ...],
    unresolved_paths: set[str],
    relocation_roots: tuple[Path, ...],
    target_user_id: str,
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
                                relocation_roots,
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
                                relocation_roots,
                                target_user_id,
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
    relocation_roots: tuple[Path, ...],
) -> bool:
    changed = False
    rows = connection.execute(
        f"SELECT rowid, {_quote(column)} FROM {_quote(table)} WHERE {_quote(column)} IS NOT NULL"
    ).fetchall()
    for rowid, value in rows:
        normalized = _normalize_path_value(
            value,
            data_roots,
            unresolved_paths,
            relocation_roots,
        )
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
    relocation_roots: tuple[Path, ...],
    target_user_id: str,
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
        normalized = _normalize_json_value(
            payload,
            data_roots,
            unresolved_paths,
            relocation_roots,
            target_user_id,
        )
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
    relocation_roots: tuple[Path, ...],
    target_user_id: str,
) -> object:
    if isinstance(value, dict):
        return {
            key: (
                target_user_id
                if str(key).lower() in _OWNERSHIP_COLUMNS
                and isinstance(item, str)
                and item.strip()
                else _normalize_path_value(
                    item,
                    data_roots,
                    unresolved_paths,
                    relocation_roots,
                )
                if str(key).lower() in _ARTIFACT_PATH_COLUMNS
                else _normalize_json_value(
                    item,
                    data_roots,
                    unresolved_paths,
                    relocation_roots,
                    target_user_id,
                )
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _normalize_json_value(
                item,
                data_roots,
                unresolved_paths,
                relocation_roots,
                target_user_id,
            )
            for item in value
        ]
    return value


def _normalize_path_value(
    value: object,
    data_roots: tuple[Path, ...],
    unresolved_paths: set[str],
    relocation_roots: tuple[Path, ...],
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
    relocated = _relocated_artifact_path(
        resolved,
        data_roots,
        relocation_roots,
    )
    if relocated is not None:
        return relocated
    unresolved_paths.add(value)
    return value


def _relocated_artifact_path(
    path: Path,
    data_roots: tuple[Path, ...],
    relocation_roots: tuple[Path, ...],
) -> str | None:
    """Recover an artifact whose absolute prefix predates a repo move/rename.

    Relocation is opt-in through explicit former repository roots. Existing
    external paths are never retargeted, even when allow-listed.
    """
    if path.exists():
        return None
    for former_repo in relocation_roots:
        for former_data_root in (former_repo / "data", former_repo / "backend" / "data"):
            try:
                relative_path = path.relative_to(former_data_root.resolve())
            except ValueError:
                continue
            if not relative_path.parts:
                continue
            for data_root in data_roots:
                root = data_root.resolve()
                candidate = (root / relative_path).resolve()
                try:
                    candidate.relative_to(root)
                except ValueError:
                    continue
                if candidate.exists():
                    return relative_path.as_posix()
    return None


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
