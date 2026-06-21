"""Regression: PDF chunks containing lone surrogates must round-trip
through the chunk-index writer without raising UnicodeEncodeError.

pypdf's ``extract_text`` occasionally yields strings with U+D800..U+DFFF
characters from broken font tables. Python's strict utf-8 encoder
refuses to write them to disk. The service must sanitize before
``write_text``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tutor.services.knowledge_base.service import (
    KnowledgeBaseService,
    _sanitize_text,
)
from tutor.services.config.settings import get_settings


def test_sanitize_text_replaces_lone_surrogates() -> None:
    bad = "ok \ud835 end"
    out = _sanitize_text(bad)
    # No lone surrogates left, and the rest of the text is preserved.
    assert "\ud835" not in out
    assert "ok" in out and "end" in out
    # Encoding must succeed.
    out.encode("utf-8")


def test_sanitize_text_passes_through_clean_text() -> None:
    s = "干净的中文 with English."
    assert _sanitize_text(s) == s


def test_sanitize_text_handles_empty() -> None:
    assert _sanitize_text("") == ""


def test_chunk_index_write_handles_surrogate_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: building a chunk index from a doc whose extracted
    text contains a lone surrogate must not raise."""
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    from tutor.services.config.settings import reset_settings_cache
    from tutor.services.knowledge_base.store import (
        get_kb_store,
        reset_kb_store,
    )
    from tutor.services.knowledge_base.schema import (
        IngestionStatus,
        KnowledgeBaseRecord,
        KnowledgeDocument,
    )

    reset_settings_cache()
    reset_kb_store()
    svc = KnowledgeBaseService()

    lib = KnowledgeBaseRecord(id="lib_x", name="X", description="")
    svc.store.upsert_library(lib)
    doc = KnowledgeDocument(
        id="doc_x",
        knowledge_base_id="lib_x",
        display_name="x.pdf",
        source_filename="x.pdf",
        extension=".pdf",
        size_bytes=10,
        checksum="0" * 64,
        status=IngestionStatus.UPLOADED,
    )
    svc.store.upsert_document(doc)

    bad_text = "A normal paragraph. \ud835 Math fragment."
    svc._write_chunk_index(
        doc,
        chunks=[{"text": bad_text, "anchor": "page 1"}],
        embeddings=[],
    )

    index_file = (
        Path(get_settings().data_dir)
        / "knowledge_bases"
        / "lib_x"
        / "sources"
        / "indexes"
        / "doc_x"
        / "chunks.json"
    )
    assert index_file.exists()
    data = json.loads(index_file.read_text(encoding="utf-8"))
    assert len(data["chunks"]) == 1
    # The bad surrogate was replaced, the rest is intact.
    assert "\ud835" not in data["chunks"][0]["text"]
    assert "A normal paragraph." in data["chunks"][0]["text"]
    assert "Math fragment." in data["chunks"][0]["text"]
