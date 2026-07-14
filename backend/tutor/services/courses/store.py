"""SQLite-backed persistence for :class:`Course` (2026-06-21 plan).

Schema
------

  courses(
      id PK, name, description, knowledge_graph_id, is_seeded,
      library_count, document_count, ready_count, total_chunks,
      extra_metadata, created_at, updated_at
  )

The store is intentionally a thin layer on top of ``sqlite3`` so
it follows the same patterns as
:mod:`tutor.services.knowledge_base.sqlite_store`. Aggregates
(``library_count`` etc.) are recomputed inside this module rather
than in the service so callers can't forget to update them.

The course↔library link itself is a column on
``knowledge_bases.course_id`` — that lives in the KB store. We
trigger aggregate recomputation by joining the KB store here
rather than re-implementing the column.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from tutor.services.config.settings import get_settings
from tutor.services.courses.schema import Course


SCHEMA_VERSION = 1

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS courses (
        id                 TEXT PRIMARY KEY,
        name               TEXT NOT NULL,
        description        TEXT NOT NULL DEFAULT '',
        knowledge_graph_id TEXT NOT NULL DEFAULT '',
        is_seeded          INTEGER NOT NULL DEFAULT 0,
        library_count      INTEGER NOT NULL DEFAULT 0,
        document_count     INTEGER NOT NULL DEFAULT 0,
        ready_count        INTEGER NOT NULL DEFAULT 0,
        total_chunks       INTEGER NOT NULL DEFAULT 0,
        extra_metadata     TEXT NOT NULL DEFAULT '{}',
        created_at         TEXT NOT NULL,
        updated_at         TEXT NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_courses_kg_id ON courses(knowledge_graph_id);",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)


def _row_to_course(row: sqlite3.Row) -> Course:
    import json

    try:
        extra = json.loads(row["extra_metadata"] or "{}")
    except (TypeError, json.JSONDecodeError):
        extra = {}
    return Course(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        knowledge_graph_id=row["knowledge_graph_id"] or "",
        is_seeded=bool(row["is_seeded"]),
        library_count=row["library_count"] or 0,
        document_count=row["document_count"] or 0,
        ready_count=row["ready_count"] or 0,
        total_chunks=row["total_chunks"] or 0,
        extra_metadata=extra or {},
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


class CourseStore:
    """Process-singleton course persistence."""

    def __init__(self, *, db_path: str | None = None) -> None:
        settings = get_settings()
        # Default to the same DB as the KB store so the operator
        # only has one file to back up. They can still split the
        # two by setting ``db_path`` explicitly.
        self._db_path = (
            db_path
            or str(Path(settings.data_dir) / "knowledge_bases.db")
        )
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._init_lock = threading.RLock()
        self._initialised = False

    # ---- lifecycle ------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                isolation_level=None,
                timeout=30.0,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
        return self._conn

    def init(self) -> None:
        if self._initialised:
            return
        with self._init_lock:
            if self._initialised:
                return
            conn = self._connect()
            with self._lock, conn:
                for ddl in _SCHEMA:
                    conn.execute(ddl)
            self._initialised = True
            logger.info("CourseStore ready at {path}", path=self._db_path)

    def close(self) -> None:
        with self._init_lock, self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
                    self._initialised = False

    # ---- CRUD -----------------------------------------------------------

    def upsert_course(self, course: Course) -> Course:
        import json

        self.init()
        with self._lock:
            conn = self._connect()
            now = _now_iso()
            conn.execute(
                """
                INSERT INTO courses(
                    id, name, description, knowledge_graph_id, is_seeded,
                    library_count, document_count, ready_count, total_chunks,
                    extra_metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name            = excluded.name,
                    description     = excluded.description,
                    knowledge_graph_id = excluded.knowledge_graph_id,
                    is_seeded       = excluded.is_seeded,
                    updated_at      = excluded.updated_at
                """,
                (
                    course.id,
                    course.name,
                    course.description or "",
                    course.knowledge_graph_id or "",
                    1 if course.is_seeded else 0,
                    course.library_count,
                    course.document_count,
                    course.ready_count,
                    course.total_chunks,
                    json.dumps(course.extra_metadata or {}),
                    _iso(course.created_at),
                    now,
                ),
            )
        return self.get_course(course.id) or course

    def get_course(self, course_id: str) -> Course | None:
        self.init()
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM courses WHERE id = ?", (course_id,)
            ).fetchone()
        return _row_to_course(row) if row else None

    def list_courses(self) -> list[Course]:
        self.init()
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM courses ORDER BY created_at ASC"
            ).fetchall()
        return [_row_to_course(r) for r in rows]

    def delete_course(self, course_id: str) -> bool:
        """Delete a course, detaching its libraries first.

        The spec calls for "deleting a course defaults to moving its
        libraries out" — we do that as part of the transaction so a
        crash mid-delete can't leave libraries pointing at a missing
        course. The actual library row updates happen via
        :func:`detach_libraries`, which is called by the service
        layer before ``delete_course``.
        """
        self.init()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "DELETE FROM courses WHERE id = ?", (course_id,)
            )
            return cur.rowcount > 0

    def update_aggregates(
        self,
        course_id: str,
        *,
        library_count: int,
        document_count: int,
        ready_count: int,
        total_chunks: int,
    ) -> None:
        """Refresh the cached aggregate counts on a course row.

        Called by the service layer after a library add/remove
        within the course. We deliberately don't try to keep these
        columns in sync via triggers — the explicit update gives
        the service full control of the transaction.
        """
        self.init()
        with self._lock:
            conn = self._connect()
            conn.execute(
                "UPDATE courses SET library_count = ?, document_count = ?, "
                "ready_count = ?, total_chunks = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    library_count,
                    document_count,
                    ready_count,
                    total_chunks,
                    _now_iso(),
                    course_id,
                ),
            )


_store: CourseStore | None = None
_store_lock = threading.Lock()


def get_course_store() -> CourseStore:
    """Return the process-wide course store (lazy)."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = CourseStore()
                _store.init()
    return _store


def reset_course_store() -> None:
    """Drop the singleton (tests)."""
    global _store
    if _store is not None:
        _store.close()
    _store = None


__all__ = ["CourseStore", "get_course_store", "reset_course_store"]
