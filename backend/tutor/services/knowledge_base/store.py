"""Deprecated in-memory KB store.

2026-06-21 plan: the metadata store for libraries and documents
moved to SQLite (see :mod:`tutor.services.knowledge_base.sqlite_store`).
This module is kept as a back-compat shim — it re-exports the new
implementation under the old names so any external code that did
``from tutor.services.knowledge_base.store import KnowledgeBaseStore``
keeps working. New code should import from
:mod:`tutor.services.knowledge_base.sqlite_store` (or the package
``__init__``).
"""

from __future__ import annotations

from tutor.services.knowledge_base.sqlite_store import (
    KnowledgeBaseSQLiteStore,
    get_kb_store,
    reset_kb_store,
)

# ``KnowledgeBaseStore`` is the historical name for the in-memory
# class; today it resolves to the SQLite implementation.
KnowledgeBaseStore = KnowledgeBaseSQLiteStore

__all__ = [
    "KnowledgeBaseStore",
    "KnowledgeBaseSQLiteStore",
    "get_kb_store",
    "reset_kb_store",
]
