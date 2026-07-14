"""SQLite-backed persistence for :mod:`tutor.services.knowledge_base`.

2026-06-21 plan: the previous in-memory dict store lost the library
and document metadata on every process restart — only the on-disk
chunk indexes survived. This module replaces that store with a
``sqlite3``-backed one that lives at
``<data_dir>/knowledge_bases.db`` and is created on first use.

Why sqlite3 (stdlib) and not SQLAlchemy
---------------------------------------
``KnowledgeBaseStore`` is a sync API; the rest of the KB service
(``KnowledgeBaseService``) is sync too. We deliberately keep the
public surface synchronous to avoid cascading async refactors, and
the stdlib ``sqlite3`` module gives us everything we need at zero
new-dependency cost. WAL is enabled so concurrent readers don't
block the ingestion task.

Schema
------

  knowledge_bases(
      id PK, name, description, is_seeded,
      document_count, ready_count, failed_count, total_chunks,
      embedding_model, course_id, created_at, updated_at
  )
  documents(
      id PK, knowledge_base_id FK, display_name, source_filename,
      extension, size_bytes, checksum, status, chunk_count,
      embedding_model, embedder_provider, embedder_dimension,
      index_version, reindex_required, embedding_warning, error,
      error_code, created_at, updated_at
  )

The 2026-06-21 columns ``embedder_provider``, ``embedder_dimension``
and ``index_version`` are the *index manifest*: they record which
embedder produced the document's vectors. ``reindex_required`` is
a flag the RAG layer sets when the runtime config no longer matches
the manifest (e.g. operator changed model or dimension).

Bootstrap from disk
-------------------
On first ``init()`` the store scans ``<data_dir>/knowledge_bases/``
for the existing layout (``<lib_id>/sources/<doc_id>.<ext>`` and
``<lib_id>/sources/indexes/<doc_id>/chunks.json``) and imports
each orphan file as a ``ready`` document. The bootstrap also
walks the ``chunks.json`` files to recover the embedding
manifest so a freshly-upgraded store can flag mismatches without
re-running the embedder.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from tutor.services.config.settings import get_settings
from tutor.services.knowledge_base.schema import (
    IngestionStatus,
    KnowledgeBaseRecord,
    KnowledgeDocument,
)


SCHEMA_VERSION = 2

# Bumped by hand whenever the embedding contract changes in a way
# that invalidates existing vectors (e.g. we change the chunking
# window, the text normaliser, or the embedding call). The RAG
# service compares this against the per-document ``index_version``
# column and flags ``reindex_required`` if they disagree.
INDEX_VERSION = 2

# Forward-only column additions for the ``documents`` table. We
# store them as a list so the migration can iterate without
# duplicating ALTER statements. Each entry has the form
# ``(column_name, column_sql)`` — the SQL after the column name.
# Adding a column here is enough; the column is added on the next
# init() if it doesn't exist. New rows always write the value,
# old rows get the column's default.
#
# 2026-06-21 fix (D5): the column migrations MUST run BEFORE any
# index that references these columns. A previous version of this
# module emitted the column-dependent index (idx_documents_reindex)
# in the same DDL list as the base schema, which caused
# ``CREATE INDEX`` to fail on a DB that already had the ``documents``
# table from an older code path (because ``CREATE TABLE IF NOT
# EXISTS`` is a no-op for an existing table). The fix is to run
# the base schema + column migrations first, then the
# post-migration indexes.
_DOCUMENT_COLUMN_MIGRATIONS: list[tuple[str, str]] = [
    ("embedder_provider", "TEXT NOT NULL DEFAULT ''"),
    ("embedder_model", "TEXT NOT NULL DEFAULT ''"),
    ("embedder_dimension", "INTEGER NOT NULL DEFAULT 0"),
    ("index_version", "INTEGER NOT NULL DEFAULT 0"),
    ("reindex_required", "INTEGER NOT NULL DEFAULT 0"),
]

# 2026-06-21 fix (D5): the schema is split in two so the
# migration order is correct.
#
# 1. ``_BASE_SCHEMA`` — table DDLs and indexes that DON'T depend on
#    any of the columns added by ``_DOCUMENT_COLUMN_MIGRATIONS``.
#    This is what runs on a brand-new database (and on an
#    up-to-date one, where every statement is a no-op).
#
# 2. ``_POST_MIGRATION_SCHEMA`` — indexes that reference columns
#    added by ``_DOCUMENT_COLUMN_MIGRATIONS``. We run this AFTER
#    the column migrations so a database that was created under an
#    older schema (no reindex_required column) gets the column
#    added before the index tries to use it.
_BASE_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS knowledge_bases (
        id              TEXT PRIMARY KEY,
        name            TEXT NOT NULL,
        description     TEXT NOT NULL DEFAULT '',
        is_seeded       INTEGER NOT NULL DEFAULT 0,
        document_count  INTEGER NOT NULL DEFAULT 0,
        ready_count     INTEGER NOT NULL DEFAULT 0,
        failed_count    INTEGER NOT NULL DEFAULT 0,
        total_chunks    INTEGER NOT NULL DEFAULT 0,
        embedding_model TEXT NOT NULL DEFAULT '',
        course_id       TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS documents (
        id                 TEXT PRIMARY KEY,
        knowledge_base_id  TEXT NOT NULL,
        display_name       TEXT NOT NULL,
        source_filename    TEXT NOT NULL,
        extension          TEXT NOT NULL,
        size_bytes         INTEGER NOT NULL DEFAULT 0,
        checksum           TEXT NOT NULL DEFAULT '',
        status             TEXT NOT NULL DEFAULT 'uploaded',
        chunk_count        INTEGER NOT NULL DEFAULT 0,
        embedding_model    TEXT NOT NULL DEFAULT '',
        embedder_provider  TEXT NOT NULL DEFAULT '',
        embedder_model     TEXT NOT NULL DEFAULT '',
        embedder_dimension INTEGER NOT NULL DEFAULT 0,
        index_version      INTEGER NOT NULL DEFAULT 0,
        reindex_required   INTEGER NOT NULL DEFAULT 0,
        embedding_warning  TEXT,
        error              TEXT,
        error_code         TEXT,
        created_at         TEXT NOT NULL,
        updated_at         TEXT NOT NULL,
        FOREIGN KEY(knowledge_base_id) REFERENCES knowledge_bases(id)
            ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_documents_kb ON documents(knowledge_base_id);",
    "CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);",
    "CREATE INDEX IF NOT EXISTS idx_kbs_course_id ON knowledge_bases(course_id);",
    # Track migrations in a tiny meta table; reserved for future use.
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]

# Indexes that depend on columns added by ``_DOCUMENT_COLUMN_MIGRATIONS``.
# These MUST run after the column migrations — see the comment on
# ``_DOCUMENT_COLUMN_MIGRATIONS`` for the full story.
_POST_MIGRATION_SCHEMA = [
    "CREATE INDEX IF NOT EXISTS idx_documents_reindex ON documents(reindex_required);",
]


def _now_iso() -> str:
    """ISO-8601 UTC timestamp with a trailing ``Z`` (SQLite stores text)."""
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


def _row_to_library(row: sqlite3.Row) -> KnowledgeBaseRecord:
    return KnowledgeBaseRecord(
        id=row["id"],
        name=row["name"],
        description=row["description"] or "",
        is_seeded=bool(row["is_seeded"]),
        document_count=row["document_count"] or 0,
        ready_count=row["ready_count"] or 0,
        failed_count=row["failed_count"] or 0,
        total_chunks=row["total_chunks"] or 0,
        embedding_model=row["embedding_model"] or "",
        # 2026-06-21 plan: carry course_id through the read path
        # too. SQLite returns the column as ``None`` for libraries
        # that have not been attached to a course.
        course_id=row["course_id"] if "course_id" in row.keys() else None,
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _row_to_document(row: sqlite3.Row) -> KnowledgeDocument:
    try:
        status = IngestionStatus(row["status"])
    except ValueError:
        status = IngestionStatus.UPLOADED
    return KnowledgeDocument(
        id=row["id"],
        knowledge_base_id=row["knowledge_base_id"],
        display_name=row["display_name"],
        source_filename=row["source_filename"],
        extension=row["extension"],
        size_bytes=row["size_bytes"] or 0,
        checksum=row["checksum"] or "",
        status=status,
        chunk_count=row["chunk_count"] or 0,
        embedding_model=row["embedding_model"] or "",
        embedder_provider=row["embedder_provider"] or "",
        embedder_model=row["embedder_model"] or "",
        embedder_dimension=row["embedder_dimension"] or 0,
        index_version=row["index_version"] or 0,
        reindex_required=bool(row["reindex_required"]),
        embedding_warning=row["embedding_warning"],
        error=row["error"],
        error_code=row["error_code"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


class KnowledgeBaseSQLiteStore:
    """Drop-in replacement for the in-memory :class:`KnowledgeBaseStore`.

    Threading model
    ---------------
    Public methods are synchronous and take a process-global lock so
    ingestion tasks and API workers don't fight over the same row.
    Writes go through a short transaction; reads use ``row_factory``
    so callers get dict-like access.

    The store is a process singleton (see :func:`get_kb_store`). The
    DB is created on first ``init()`` and the on-disk source
    directories are walked for migration.
    """

    def __init__(self, *, db_path: str | None = None) -> None:
        settings = get_settings()
        self._db_path = (
            db_path
            or str(Path(settings.data_dir) / "knowledge_bases.db")
        )
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        # RLock so the bootstrap loop can call back into init() via
        # ``upsert_library`` / ``upsert_document`` without deadlocking
        # the thread that originally entered ``init()``.
        self._init_lock = threading.RLock()
        self._initialised = False

    # ---- lifecycle ------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                isolation_level=None,  # autocommit; we manage txns explicitly
                timeout=30.0,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.execute("PRAGMA synchronous=NORMAL;")
        return self._conn

    def init(self) -> None:
        """Create tables (idempotent) and bootstrap from on-disk state.

        Safe to call repeatedly. Returns once the DB is ready; the
        actual work is a one-time migration on first call.

        2026-06-21 fix (D5): the order is now

          1. ``_BASE_SCHEMA``  — table DDLs + safe indexes
          2. ``_apply_document_column_migrations``  — ALTER TABLE
             for any column missing on a pre-existing ``documents``
             table
          3. ``_POST_MIGRATION_SCHEMA``  — indexes that reference
             the newly-added columns

        The pre-fix code emitted the column-dependent index in step
        1, which crashed on any database whose ``documents`` table
        predated the column migration.
        """
        if self._initialised:
            return
        # RLock so reentrant calls from ``upsert_library`` /
        # ``upsert_document`` (invoked by the bootstrap loop) don't
        # deadlock the very thread that holds the init lock.
        with self._init_lock:
            if self._initialised:
                return
            # Mark initialised early so any reentrant ``init()``
            # calls coming through upsert_* during bootstrap short
            # circuit to the (no-op) early return. The bootstrap
            # itself only depends on the schema being created, which
            # we finish before flipping the flag.
            conn = self._connect()
            with self._lock, conn:
                # Step 1: tables and indexes that don't depend on
                # the new columns.
                for ddl in _BASE_SCHEMA:
                    conn.execute(ddl)
                # Step 2: ALTER TABLE for the columns a pre-existing
                # ``documents`` table may be missing. After this
                # runs, the table is guaranteed to have the
                # 2026-06-21 columns.
                self._apply_document_column_migrations(conn)
                # Step 3: indexes that depend on the new columns.
                for ddl in _POST_MIGRATION_SCHEMA:
                    conn.execute(ddl)
                # Bump the schema version. A future migration that
                # depends on ``SCHEMA_VERSION`` (e.g. dropping
                # legacy columns) can read this to know whether it
                # needs to run.
                conn.execute(
                    "INSERT OR REPLACE INTO schema_meta(key, value) VALUES (?, ?)",
                    ("schema_version", str(SCHEMA_VERSION)),
                )
            # Bootstrap from the on-disk layout. This is the migration
            # path for upgrades from the in-memory store: we scan
            # ``<data_dir>/knowledge_bases/`` and import any orphan
            # files we don't already know about. We hold the init
            # lock (RLock) for the whole bootstrap so a concurrent
            # ``init()`` from another thread blocks until the import
            # finishes — otherwise the second thread could see the
            # schema but not the imported documents.
            self._initialised = True
            self._bootstrap_from_disk()
            logger.info(
                "KnowledgeBaseSQLiteStore ready at {path}",
                path=self._db_path,
            )

    def _apply_document_column_migrations(
        self, conn: sqlite3.Connection
    ) -> None:
        """Add any missing 2026-06-21 columns to a pre-existing
        ``documents`` table.

        The 2026-06-21 plan added ``embedder_provider``,
        ``embedder_dimension``, ``index_version`` and
        ``reindex_required`` to the document manifest. A DB created
        by an earlier version of the project won't have these
        columns, and ``CREATE TABLE IF NOT EXISTS`` is a no-op on
        an existing table — so we explicitly ``ALTER TABLE ADD
        COLUMN`` for any column that's missing. SQLite supports
        this in 3.2+ and is a no-op (re-raising) when the column
        already exists, which we handle by catching the error and
        continuing.
        """
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        for col, sql in _DOCUMENT_COLUMN_MIGRATIONS:
            if col in existing:
                continue
            logger.info("migration: documents ADD COLUMN {col}", col=col)
            conn.execute(f"ALTER TABLE documents ADD COLUMN {col} {sql}")

    def close(self) -> None:
        with self._init_lock, self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
                    self._initialised = False

    def _bootstrap_from_disk(self) -> None:
        """Walk the on-disk KB layout and import any orphan files.

        This is what keeps the 2026-06-21 upgrade path smooth: a
        process that was running the in-memory store has its source
        files at ``<data_dir>/knowledge_bases/<lib_id>/sources/...``.
        On first init we register a synthetic library for every
        sub-directory that isn't already in the DB, and a synthetic
        document for every source file with no matching id.

        ``is_seeded`` is True for libraries the on-disk scan created
        — the next time the operator runs the official seed
        migration, those rows stay around and just lose their
        ``is_seeded`` flag.
        """
        settings = get_settings()
        base = Path(settings.data_dir) / "knowledge_bases"
        if not base.exists():
            return
        for lib_dir in sorted(base.iterdir()):
            if not lib_dir.is_dir():
                continue
            lib_id = lib_dir.name
            sources = lib_dir / "sources"
            if not sources.exists():
                continue
            lib = self.get_library(lib_id)
            if lib is None:
                # Synthesize a library. We can't recover its
                # human-readable name, so we fall back to the lib_id
                # and let the operator rename later via the API.
                rec = KnowledgeBaseRecord(
                    id=lib_id,
                    name=lib_id,
                    description="[imported from on-disk state]",
                    is_seeded=False,
                )
                self.upsert_library(rec)
            for f in sorted(sources.iterdir()):
                if not f.is_file() or not f.name.startswith("doc_"):
                    continue
                if f.suffix.lower() not in {".pdf", ".docx", ".pptx", ".md", ".txt"}:
                    continue
                doc_id = f.stem  # e.g. "doc_ab12cd34ef56"
                if self.get_document(doc_id) is not None:
                    continue
                chunk_count = 0
                embedding_model = ""
                embedder_provider = ""
                embedder_dimension = 0
                index_version = 0
                reindex_required = False
                index_path = sources / "indexes" / doc_id / "chunks.json"
                if index_path.exists():
                    try:
                        payload = json.loads(index_path.read_text(encoding="utf-8"))
                        chunk_count = len(payload.get("chunks", []))
                        # The old format used a single ``embedding_model``
                        # field; the 2026-06-21 manifest has
                        # ``embedder_provider`` + ``embedder_dimension``
                        # + ``index_version``. We accept either shape.
                        embedder_provider = (
                            payload.get("embedder_provider", "") or ""
                        )
                        embedder_dimension = int(
                            payload.get("embedder_dimension", 0) or 0
                        )
                        index_version = int(payload.get("index_version", 0) or 0)
                        embedding_model = (
                            payload.get("embedding_model", "") or embedder_provider
                        )
                        if index_version and index_version != INDEX_VERSION:
                            # Old chunks.json — they were indexed with
                            # an older contract; flag for reindex.
                            reindex_required = True
                    except (OSError, json.JSONDecodeError):
                        pass
                try:
                    size = f.stat().st_size
                except OSError:
                    size = 0
                checksum = ""
                try:
                    h = hashlib.sha256()
                    with f.open("rb") as fh:
                        for chunk in iter(lambda: fh.read(64 * 1024), b""):
                            h.update(chunk)
                    checksum = h.hexdigest()
                except OSError:
                    pass
                doc = KnowledgeDocument(
                    id=doc_id,
                    knowledge_base_id=lib_id,
                    display_name=f.name,
                    source_filename=f.name,
                    extension=f.suffix.lower(),
                    size_bytes=size,
                    checksum=checksum,
                    status=IngestionStatus.READY,
                    chunk_count=chunk_count,
                    embedding_model=embedding_model,
                    embedder_provider=embedder_provider,
                    embedder_model=embedding_model,
                    embedder_dimension=embedder_dimension,
                    index_version=index_version,
                    reindex_required=reindex_required,
                )
                self.upsert_document(doc)
            # After the import, recompute the library's aggregate
            # counts so the API / UI see the real numbers immediately.
            self._recompute_library_counts(lib_id)

    # ---- libraries ------------------------------------------------------

    def upsert_library(self, lib: KnowledgeBaseRecord) -> KnowledgeBaseRecord:
        self.init()
        with self._lock:
            conn = self._connect()
            now = _now_iso()
            conn.execute(
                """
                INSERT INTO knowledge_bases(
                    id, name, description, is_seeded, document_count,
                    ready_count, failed_count, total_chunks,
                    embedding_model, course_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name            = excluded.name,
                    description     = excluded.description,
                    is_seeded       = excluded.is_seeded,
                    document_count  = excluded.document_count,
                    ready_count     = excluded.ready_count,
                    failed_count    = excluded.failed_count,
                    total_chunks    = excluded.total_chunks,
                    embedding_model = excluded.embedding_model,
                    course_id       = excluded.course_id,
                    updated_at      = excluded.updated_at
                """,
                (
                    lib.id,
                    lib.name,
                    lib.description or "",
                    1 if lib.is_seeded else 0,
                    lib.document_count,
                    lib.ready_count,
                    lib.failed_count,
                    lib.total_chunks,
                    lib.embedding_model or "",
                    getattr(lib, "course_id", None),
                    _iso(lib.created_at),
                    now,
                ),
            )
        # Read back so the caller sees the persisted timestamps.
        fresh = self.get_library(lib.id)
        return fresh or lib

    def get_library(self, lib_id: str) -> KnowledgeBaseRecord | None:
        self.init()
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM knowledge_bases WHERE id = ?", (lib_id,)
            ).fetchone()
        return _row_to_library(row) if row else None

    def list_libraries(self) -> list[KnowledgeBaseRecord]:
        self.init()
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM knowledge_bases ORDER BY created_at ASC"
            ).fetchall()
        return [_row_to_library(r) for r in rows]

    def delete_library(self, lib_id: str) -> bool:
        self.init()
        with self._lock:
            conn = self._connect()
            conn.execute("BEGIN")
            try:
                # Documents first because of the FK.
                conn.execute(
                    "DELETE FROM documents WHERE knowledge_base_id = ?",
                    (lib_id,),
                )
                cur = conn.execute(
                    "DELETE FROM knowledge_bases WHERE id = ?", (lib_id,)
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return cur.rowcount > 0

    def set_library_course(self, lib_id: str, course_id: str | None) -> bool:
        """Update a library's ``course_id``. Pass ``None`` to detach.

        Returns False if the library does not exist. Used by the
        course-membership endpoints in the RAG-overhaul plan
        (Part D). The constraint "at most one course per library" is
        trivially satisfied by a single column.
        """
        self.init()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE knowledge_bases SET course_id = ?, updated_at = ? "
                "WHERE id = ?",
                (course_id, _now_iso(), lib_id),
            )
            return cur.rowcount > 0

    # ---- documents ------------------------------------------------------

    def upsert_document(self, doc: KnowledgeDocument) -> KnowledgeDocument:
        self.init()
        with self._lock:
            conn = self._connect()
            now = _now_iso()
            conn.execute(
                """
                INSERT INTO documents(
                    id, knowledge_base_id, display_name, source_filename,
                    extension, size_bytes, checksum, status, chunk_count,
                    embedding_model, embedder_provider, embedder_model,
                    embedder_dimension, index_version, reindex_required,
                    embedding_warning, error, error_code,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    knowledge_base_id = excluded.knowledge_base_id,
                    display_name      = excluded.display_name,
                    source_filename   = excluded.source_filename,
                    extension         = excluded.extension,
                    size_bytes        = excluded.size_bytes,
                    checksum          = excluded.checksum,
                    status            = excluded.status,
                    chunk_count       = excluded.chunk_count,
                    embedding_model   = excluded.embedding_model,
                    embedder_provider   = excluded.embedder_provider,
                    embedder_model      = excluded.embedder_model,
                    embedder_dimension  = excluded.embedder_dimension,
                    index_version       = excluded.index_version,
                    reindex_required    = excluded.reindex_required,
                    embedding_warning = excluded.embedding_warning,
                    error             = excluded.error,
                    error_code        = excluded.error_code,
                    updated_at        = excluded.updated_at
                """,
                (
                    doc.id,
                    doc.knowledge_base_id,
                    doc.display_name,
                    doc.source_filename,
                    doc.extension,
                    doc.size_bytes,
                    doc.checksum or "",
                    doc.status.value,
                    doc.chunk_count,
                    doc.embedding_model or "",
                    doc.embedder_provider or "",
                    doc.embedder_model or "",
                    int(doc.embedder_dimension or 0),
                    int(doc.index_version or 0),
                    1 if doc.reindex_required else 0,
                    doc.embedding_warning,
                    doc.error,
                    doc.error_code,
                    _iso(doc.created_at),
                    now,
                ),
            )
            self._recompute_library_counts(doc.knowledge_base_id)
        fresh = self.get_document(doc.id)
        return fresh or doc

    def get_document(self, doc_id: str) -> KnowledgeDocument | None:
        self.init()
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
        return _row_to_document(row) if row else None

    def list_documents(self, lib_id: str) -> list[KnowledgeDocument]:
        self.init()
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM documents WHERE knowledge_base_id = ? "
                "ORDER BY created_at ASC",
                (lib_id,),
            ).fetchall()
        return [_row_to_document(r) for r in rows]

    def delete_document(self, doc_id: str) -> bool:
        self.init()
        with self._lock:
            conn = self._connect()
            conn.execute("BEGIN")
            try:
                # Capture the lib_id before deletion so we can update
                # the parent library's aggregate counts.
                row = conn.execute(
                    "SELECT knowledge_base_id FROM documents WHERE id = ?",
                    (doc_id,),
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return False
                conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            self._recompute_library_counts(row["knowledge_base_id"])
            return True

    def set_document_status(
        self,
        doc_id: str,
        *,
        status: IngestionStatus,
        chunk_count: int | None = None,
        embedding_model: str | None = None,
        embedding_warning: str | None = None,
        error: str | None = None,
        error_code: str | None = None,
        embedder_provider: str | None = None,
        embedder_model: str | None = None,
        embedder_dimension: int | None = None,
        index_version: int | None = None,
        reindex_required: bool | None = None,
    ) -> KnowledgeDocument | None:
        self.init()
        with self._lock:
            conn = self._connect()
            conn.execute("BEGIN")
            try:
                row = conn.execute(
                    "SELECT * FROM documents WHERE id = ?", (doc_id,)
                ).fetchone()
                if row is None:
                    conn.execute("ROLLBACK")
                    return None
                lib_id = row["knowledge_base_id"]
                # Build the UPDATE column-by-column so callers can
                # update just one field without clobbering the rest.
                sets: list[str] = ["status = ?", "updated_at = ?"]
                params: list[Any] = [status.value, _now_iso()]
                if chunk_count is not None:
                    sets.append("chunk_count = ?")
                    params.append(chunk_count)
                if embedding_model is not None:
                    sets.append("embedding_model = ?")
                    params.append(embedding_model)
                if embedder_provider is not None:
                    sets.append("embedder_provider = ?")
                    params.append(embedder_provider)
                if embedder_model is not None:
                    sets.append("embedder_model = ?")
                    params.append(embedder_model)
                if embedder_dimension is not None:
                    sets.append("embedder_dimension = ?")
                    params.append(int(embedder_dimension))
                if index_version is not None:
                    sets.append("index_version = ?")
                    params.append(int(index_version))
                if reindex_required is not None:
                    sets.append("reindex_required = ?")
                    params.append(1 if reindex_required else 0)
                if embedding_warning is not None:
                    sets.append("embedding_warning = ?")
                    params.append(embedding_warning)
                if error is not None:
                    sets.append("error = ?")
                    params.append(error)
                if error_code is not None:
                    sets.append("error_code = ?")
                    params.append(error_code)
                params.append(doc_id)
                conn.execute(
                    f"UPDATE documents SET {', '.join(sets)} WHERE id = ?",
                    params,
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            self._recompute_library_counts(lib_id)
        return self.get_document(doc_id)

    def mark_reindex_required(self, *, embedder_provider: str, embedder_dimension: int) -> int:
        """Bulk-flag documents whose manifest no longer matches the runtime config.

        Walks every ``ready`` document and sets
        ``reindex_required = 1`` when:

          * ``embedder_provider`` differs from the runtime config, or
          * ``embedder_dimension`` differs from the runtime config
            (and is non-zero — zero means "no vectors were ever
            embedded", which is a separate failure mode the
            ingestion service reports via ``embedding_warning``).

        Returns the number of documents that were flagged. The
        caller is expected to log this so an operator can decide
        whether to reindex or roll the config back.
        """
        self.init()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                "UPDATE documents SET reindex_required = 1, updated_at = ? "
                "WHERE status = ? AND "
                "(embedder_provider != ? OR "
                "(embedder_dimension != 0 AND embedder_dimension != ?))",
                (
                    _now_iso(),
                    IngestionStatus.READY.value,
                    embedder_provider,
                    int(embedder_dimension),
                ),
            )
            return cur.rowcount

    def count_reindex_required(self) -> int:
        self.init()
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM documents WHERE reindex_required = 1"
            ).fetchone()
            return int(row["n"]) if row else 0

    # ---- internals ------------------------------------------------------

    def _recompute_library_counts(self, lib_id: str) -> None:
        """Recompute ``document_count`` / ``ready_count`` / etc. on the
        parent library. This is what keeps the UI's KB card badges
        consistent with the actual document set after every state
        transition.
        """
        conn = self._connect()
        row = conn.execute(
            "SELECT status, chunk_count FROM documents WHERE knowledge_base_id = ?",
            (lib_id,),
        ).fetchall()
        if not row:
            # Library has no documents — zero the aggregates.
            conn.execute(
                "UPDATE knowledge_bases SET document_count = 0, "
                "ready_count = 0, failed_count = 0, total_chunks = 0, "
                "updated_at = ? WHERE id = ?",
                (_now_iso(), lib_id),
            )
            return
        doc_count = len(row)
        ready = sum(1 for r in row if r["status"] == IngestionStatus.READY.value)
        failed = sum(1 for r in row if r["status"] == IngestionStatus.FAILED.value)
        total_chunks = sum(r["chunk_count"] or 0 for r in row)
        conn.execute(
            "UPDATE knowledge_bases SET document_count = ?, ready_count = ?, "
            "failed_count = ?, total_chunks = ?, updated_at = ? WHERE id = ?",
            (doc_count, ready, failed, total_chunks, _now_iso(), lib_id),
        )


_store: KnowledgeBaseSQLiteStore | None = None
_store_lock = threading.Lock()


def get_kb_store() -> KnowledgeBaseSQLiteStore:
    """Return the process-wide KB store (lazy)."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = KnowledgeBaseSQLiteStore()
                # ``init()`` is cheap and idempotent. We don't
                # block on the bootstrap scan; the rare race of two
                # callers initialising at once is handled by the
                # internal lock.
                _store.init()
    return _store


def reset_kb_store() -> None:
    """Drop the singleton (tests)."""
    global _store
    if _store is not None:
        _store.close()
    _store = None


__all__ = [
    "KnowledgeBaseSQLiteStore",
    "get_kb_store",
    "reset_kb_store",
]
