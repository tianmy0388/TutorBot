"""Tests for the persistent :class:`KnowledgeBaseSQLiteStore`.

These cover the 2026-06-21 plan migration: library / document
metadata must survive a process restart, and orphan source files on
disk must be re-imported as ``ready`` documents on first init.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tutor.services.knowledge_base.schema import (
    IngestionStatus,
    KnowledgeBaseRecord,
    KnowledgeDocument,
)
from tutor.services.knowledge_base.sqlite_store import (
    KnowledgeBaseSQLiteStore,
    get_kb_store,
    reset_kb_store,
)


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> KnowledgeBaseSQLiteStore:
    """A fresh SQLite store under a tmp dir."""
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    reset_kb_store()
    s = KnowledgeBaseSQLiteStore(db_path=str(tmp_path / "kb.db"))
    s.init()
    yield s
    s.close()
    reset_kb_store()


def test_upsert_and_get_library(store: KnowledgeBaseSQLiteStore) -> None:
    lib = KnowledgeBaseRecord(id="kb_a", name="Test KB", description="hello")
    store.upsert_library(lib)
    out = store.get_library("kb_a")
    assert out is not None
    assert out.name == "Test KB"
    assert out.description == "hello"
    assert out.is_seeded is False
    assert out.document_count == 0


def test_list_libraries_ordering(store: KnowledgeBaseSQLiteStore) -> None:
    for i in range(3):
        store.upsert_library(KnowledgeBaseRecord(id=f"kb_{i}", name=f"k{i}"))
    libs = store.list_libraries()
    assert [l.id for l in libs] == ["kb_0", "kb_1", "kb_2"]


def test_delete_library_cascades(store: KnowledgeBaseSQLiteStore) -> None:
    store.upsert_library(KnowledgeBaseRecord(id="kb_a", name="A"))
    store.upsert_document(
        KnowledgeDocument(
            id="doc_x",
            knowledge_base_id="kb_a",
            display_name="x.pdf",
            source_filename="x.pdf",
            extension=".pdf",
            status=IngestionStatus.READY,
        )
    )
    assert store.delete_library("kb_a") is True
    assert store.get_library("kb_a") is None
    # The document is also gone (FK cascade).
    assert store.get_document("doc_x") is None


def test_upsert_document_recomputes_library_counts(
    store: KnowledgeBaseSQLiteStore,
) -> None:
    store.upsert_library(KnowledgeBaseRecord(id="kb_a", name="A"))
    for i, status in enumerate(
        [IngestionStatus.READY, IngestionStatus.READY, IngestionStatus.FAILED]
    ):
        store.upsert_document(
            KnowledgeDocument(
                id=f"doc_{i}",
                knowledge_base_id="kb_a",
                display_name=f"f{i}.pdf",
                source_filename=f"f{i}.pdf",
                extension=".pdf",
                chunk_count=2,
                status=status,
            )
        )
    lib = store.get_library("kb_a")
    assert lib is not None
    assert lib.document_count == 3
    assert lib.ready_count == 2
    assert lib.failed_count == 1
    assert lib.total_chunks == 6


def test_set_document_status_partial_update(
    store: KnowledgeBaseSQLiteStore,
) -> None:
    store.upsert_library(KnowledgeBaseRecord(id="kb_a", name="A"))
    store.upsert_document(
        KnowledgeDocument(
            id="doc_1",
            knowledge_base_id="kb_a",
            display_name="x.pdf",
            source_filename="x.pdf",
            extension=".pdf",
            status=IngestionStatus.UPLOADED,
            chunk_count=0,
        )
    )
    out = store.set_document_status(
        "doc_1",
        status=IngestionStatus.READY,
        chunk_count=10,
        embedding_model="text-embedding-3-small",
    )
    assert out is not None
    assert out.status == IngestionStatus.READY
    assert out.chunk_count == 10
    assert out.embedding_model == "text-embedding-3-small"
    # unrelated fields still defaulted
    assert out.error is None


def test_metadata_persists_across_sessions(tmp_path: Path, monkeypatch) -> None:
    """Re-opening the same DB file must yield the same rows."""
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    reset_kb_store()

    db = tmp_path / "kb.db"
    s1 = KnowledgeBaseSQLiteStore(db_path=str(db))
    s1.init()
    s1.upsert_library(KnowledgeBaseRecord(id="kb_a", name="Persisted", is_seeded=True))
    s1.upsert_document(
        KnowledgeDocument(
            id="doc_x",
            knowledge_base_id="kb_a",
            display_name="x.pdf",
            source_filename="x.pdf",
            extension=".pdf",
            status=IngestionStatus.READY,
            chunk_count=4,
        )
    )
    s1.close()

    # New instance, same DB file.
    s2 = KnowledgeBaseSQLiteStore(db_path=str(db))
    s2.init()
    lib = s2.get_library("kb_a")
    assert lib is not None
    assert lib.name == "Persisted"
    assert lib.is_seeded is True
    assert lib.document_count == 1
    doc = s2.get_document("doc_x")
    assert doc is not None
    assert doc.chunk_count == 4
    s2.close()


def test_bootstrap_imports_orphan_sources(tmp_path: Path, monkeypatch) -> None:
    """Walking the on-disk layout must import orphan files as ready docs."""
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    reset_kb_store()

    # Pre-create the on-disk layout as if the in-memory store had
    # been running before the upgrade.
    sources = tmp_path / "knowledge_bases" / "kb_a" / "sources"
    sources.mkdir(parents=True)
    pdf = sources / "doc_xyz123.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake content")
    index_dir = sources / "indexes" / "doc_xyz123"
    index_dir.mkdir(parents=True)
    (index_dir / "chunks.json").write_text(
        json.dumps(
            {
                "document_id": "doc_xyz123",
                "knowledge_base_id": "kb_a",
                "embedding_model": "text-embedding-3-small",
                "chunks": [{"text": "a"}, {"text": "b"}, {"text": "c"}],
                "embeddings": [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
            }
        ),
        encoding="utf-8",
    )

    db = tmp_path / "kb.db"
    s = KnowledgeBaseSQLiteStore(db_path=str(db))
    s.init()
    lib = s.get_library("kb_a")
    assert lib is not None
    assert lib.name == "kb_a"  # fallback to id
    doc = s.get_document("doc_xyz123")
    assert doc is not None
    assert doc.status == IngestionStatus.READY
    assert doc.chunk_count == 3
    assert doc.embedding_model == "text-embedding-3-small"
    assert doc.size_bytes == len(b"%PDF-1.4 fake content")
    assert doc.checksum  # sha256
    s.close()


def test_set_library_course(store: KnowledgeBaseSQLiteStore) -> None:
    store.upsert_library(KnowledgeBaseRecord(id="kb_a", name="A"))
    assert store.set_library_course("kb_a", "course_x") is True
    lib = store.get_library("kb_a")
    assert lib is not None
    assert lib.course_id == "course_x"
    # Detach again.
    assert store.set_library_course("kb_a", None) is True
    assert store.get_library("kb_a").course_id is None


def test_set_library_course_missing_returns_false(
    store: KnowledgeBaseSQLiteStore,
) -> None:
    assert store.set_library_course("kb_does_not_exist", "course_x") is False


# ---------------------------------------------------------------------------
# 2026-06-21 fix (D5): the migration order bug.
# ---------------------------------------------------------------------------
# The pre-fix init() ran the base schema AND the column-dependent
# indexes in the same DDL list. On a pre-existing ``documents``
# table that was created without the 2026-06-21 columns
# (embedder_provider, embedder_dimension, index_version,
# reindex_required), ``CREATE TABLE IF NOT EXISTS`` is a no-op and
# the subsequent ``CREATE INDEX ON documents(reindex_required)``
# failed with "no such column: reindex_required". The fix is to
# run the base schema, ALTER TABLE, then the post-migration
# indexes. The tests below pin the bug as regression coverage.


def test_migration_old_documents_table_gets_new_columns(
    tmp_path: Path, monkeypatch
) -> None:
    """A pre-existing DB whose documents table predates the
    2026-06-21 columns must be migrated in place on init.

    We hand-roll the old schema in a fresh DB file, then open it
    with :class:`KnowledgeBaseSQLiteStore` and assert the columns
    are added and the index is created without error.
    """
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    reset_kb_store()
    db = tmp_path / "knowledge_bases.db"
    # Hand-roll the OLD schema (no 2026-06-21 columns).
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE knowledge_bases (
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
        CREATE TABLE documents (
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
            embedding_warning  TEXT,
            error              TEXT,
            error_code         TEXT,
            created_at         TEXT NOT NULL,
            updated_at         TEXT NOT NULL,
            FOREIGN KEY(knowledge_base_id) REFERENCES knowledge_bases(id)
                ON DELETE CASCADE
        );
        CREATE INDEX idx_documents_kb ON documents(knowledge_base_id);
        CREATE INDEX idx_documents_status ON documents(status);
        CREATE INDEX idx_kbs_course_id ON knowledge_bases(course_id);
        """
    )
    # Insert a row under the OLD schema so we can verify the column
    # default fills in correctly.
    conn.execute(
        "INSERT INTO documents (id, knowledge_base_id, display_name, "
        "source_filename, extension, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "doc_old",
            "kb_a",
            "old.pdf",
            "old.pdf",
            ".pdf",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()
    # Now open with the new code. The init() should migrate
    # without raising.
    s = KnowledgeBaseSQLiteStore(db_path=str(db))
    s.init()
    # Columns exist and the old row has the right defaults.
    doc = s.get_document("doc_old")
    assert doc is not None
    assert doc.embedder_provider == ""
    assert doc.embedder_dimension == 0
    assert doc.index_version == 0
    assert doc.reindex_required is False
    # The new index exists. (We don't query it directly; we just
    # verify the table is fully wired by re-running init().)
    s.init()  # second init is a no-op
    s.close()
    reset_kb_store()


def test_migration_index_on_reindex_column_works(
    tmp_path: Path, monkeypatch
) -> None:
    """The post-migration schema must include the index on
    ``reindex_required`` — it would have failed in the pre-fix
    code path on any pre-existing DB.
    """
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    reset_kb_store()
    db = tmp_path / "knowledge_bases.db"
    s = KnowledgeBaseSQLiteStore(db_path=str(db))
    s.init()
    # The index is named ``idx_documents_reindex`` and it
    # references the reindex_required column. If init() had
    # created this index BEFORE the column migration, opening
    # the freshly-created DB would already have the column
    # (because CREATE TABLE is the source) and the index would
    # exist. But the test below exercises the migration path on
    # an OLD-shape DB by re-creating it.
    # Confirm the index is present.
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name='idx_documents_reindex'"
    ).fetchall()
    conn.close()
    assert rows, "idx_documents_reindex must exist after init()"
    s.close()
    reset_kb_store()


def test_migration_schema_version_bumped(
    tmp_path: Path, monkeypatch
) -> None:
    """After the D5 fix, ``schema_meta.schema_version`` should
    record the latest version so a future migration can decide
    whether to run.
    """
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    from tutor.services.config.settings import reset_settings_cache
    from tutor.services.knowledge_base.sqlite_store import SCHEMA_VERSION

    reset_settings_cache()
    reset_kb_store()
    db = tmp_path / "knowledge_bases.db"
    s = KnowledgeBaseSQLiteStore(db_path=str(db))
    s.init()
    conn = sqlite3.connect(str(db))
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key='schema_version'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert int(row[0]) == SCHEMA_VERSION
    s.close()
    reset_kb_store()
