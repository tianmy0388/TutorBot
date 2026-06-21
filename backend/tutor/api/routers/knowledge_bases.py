"""Knowledge base HTTP endpoints (Task 8)."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from tutor.services.knowledge_base import (
    IngestionStatus,
    KnowledgeBaseRecord,
    KnowledgeBaseService,
    SUPPORTED_EXTENSIONS,
    get_kb_store,
    seed_default_libraries,
)

router = APIRouter()
_service = KnowledgeBaseService()


class CreateLibraryRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)


def _ensure_seeded() -> None:
    seed_default_libraries(_service)


@router.get("/knowledge-bases")
async def list_knowledge_bases() -> dict[str, Any]:
    _ensure_seeded()
    libs = _service.list_libraries()
    return {"items": [lib.model_dump(mode="json") for lib in libs], "total": len(libs)}


@router.post("/knowledge-bases", status_code=201)
async def create_knowledge_base(req: CreateLibraryRequest) -> dict[str, Any]:
    _ensure_seeded()
    lib = _service.create_library(name=req.name, description=req.description)
    return lib.model_dump(mode="json")


@router.get("/knowledge-bases/{lib_id}")
async def get_knowledge_base(lib_id: str) -> dict[str, Any]:
    _ensure_seeded()
    lib = _service.get_library(lib_id)
    if lib is None:
        raise HTTPException(status_code=404, detail="library not found")
    docs = _service.list_documents(lib_id)
    return {
        **lib.model_dump(mode="json"),
        "documents": [d.model_dump(mode="json") for d in docs],
    }


@router.delete("/knowledge-bases/{lib_id}")
async def delete_knowledge_base(lib_id: str) -> dict[str, Any]:
    ok = _service.delete_library(lib_id)
    if not ok:
        raise HTTPException(status_code=404, detail="library not found")
    return {"deleted": True, "id": lib_id}


@router.post("/knowledge-bases/{lib_id}/documents", status_code=202)
async def upload_document(
    lib_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Persist the upload, then dispatch ingestion to a background task.

    Returns 202 with the document in the ``uploaded`` state. The
    actual extraction / chunking / embedding runs in a bounded
    ``asyncio.Task`` so the request thread is freed immediately.
    Clients poll ``GET /knowledge-bases/{lib_id}`` to see the
    state-machine transitions.
    """
    _ensure_seeded()
    if _service.get_library(lib_id) is None:
        raise HTTPException(status_code=404, detail="library not found")
    if not file.filename:
        raise HTTPException(status_code=422, detail="missing filename")
    ext = Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "UNSUPPORTED_EXTENSION",
                "message": f"unsupported extension {ext!r}",
                "allowed": sorted(SUPPORTED_EXTENSIONS),
            },
        )
    # Save the upload to a temp file first so the service can copy it.
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)
    try:
        doc = _service.upload_document(
            knowledge_base_id=lib_id,
            source_path=tmp_path,
            original_filename=file.filename,
        )
    finally:
        try:
            tmp_path.unlink()
        except OSError:  # noqa: BLE001
            pass
    # Dispatch the ingestion pipeline as a background task. The
    # response goes back to the client immediately with the document
    # in 'uploaded' state; subsequent state transitions land in the
    # KB store and the client picks them up via the polling endpoint.
    _service.enqueue_ingestion(doc.id)
    return doc.model_dump(mode="json")


@router.post("/knowledge-bases/{lib_id}/documents/{doc_id}/retry")
async def retry_document(lib_id: str, doc_id: str) -> dict[str, Any]:
    doc = _service.retry_document(doc_id)
    if doc is None or doc.knowledge_base_id != lib_id:
        raise HTTPException(status_code=404, detail="document not found")
    return doc.model_dump(mode="json")


@router.delete("/knowledge-bases/{lib_id}/documents/{doc_id}")
async def delete_document(lib_id: str, doc_id: str) -> dict[str, Any]:
    doc = _service.get_document(doc_id)
    if doc is None or doc.knowledge_base_id != lib_id:
        raise HTTPException(status_code=404, detail="document not found")
    ok = _service.delete_document(doc_id)
    return {"deleted": ok, "id": doc_id}


__all__ = ["router"]
