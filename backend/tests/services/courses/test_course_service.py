"""Tests for the Course service (2026-06-21 plan, Part D)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tutor.services.courses import (
    CourseService,
    get_course_service,
    get_course_store,
    reset_course_service,
    reset_course_store,
    seed_default_courses,
)
from tutor.services.courses.schema import Course
from tutor.services.courses.store import CourseStore
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
def fresh(tmp_path: Path, monkeypatch) -> tuple[CourseStore, KnowledgeBaseSQLiteStore]:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path))
    from tutor.services.config.settings import reset_settings_cache

    reset_settings_cache()
    reset_course_store()
    reset_kb_store()
    reset_course_service()

    db = tmp_path / "kb.db"
    cs = CourseStore(db_path=str(db))
    cs.init()
    ks = KnowledgeBaseSQLiteStore(db_path=str(db))
    ks.init()
    yield cs, ks
    cs.close()
    ks.close()
    reset_course_store()
    reset_kb_store()
    reset_course_service()


def test_upsert_and_get_course(fresh) -> None:
    cs, _ = fresh
    course = Course(id="course_x", name="Test Course", description="hi")
    cs.upsert_course(course)
    out = cs.get_course("course_x")
    assert out is not None
    assert out.name == "Test Course"
    assert out.library_count == 0


def test_attach_library_updates_aggregates(fresh) -> None:
    cs, ks = fresh
    cs.upsert_course(Course(id="course_a", name="A"))
    ks.upsert_library(KnowledgeBaseRecord(id="kb_x", name="X"))
    ks.upsert_document(
        KnowledgeDocument(
            id="doc_1",
            knowledge_base_id="kb_x",
            display_name="x.pdf",
            source_filename="x.pdf",
            extension=".pdf",
            status=IngestionStatus.READY,
            chunk_count=3,
        )
    )
    svc = CourseService(store=cs, kb_store=ks)
    out = svc.attach_library("course_a", "kb_x")
    assert out is not None
    assert out.library_count == 1
    assert out.document_count == 1
    assert out.ready_count == 1
    assert out.total_chunks == 3
    # Library now points at the course.
    lib = ks.get_library("kb_x")
    assert lib is not None
    assert lib.course_id == "course_a"


def test_attach_to_a_second_course_implicitly_detaches(
    fresh,
) -> None:
    """A library may belong to at most one course at a time."""
    cs, ks = fresh
    cs.upsert_course(Course(id="course_a", name="A"))
    cs.upsert_course(Course(id="course_b", name="B"))
    ks.upsert_library(KnowledgeBaseRecord(id="kb_x", name="X"))
    svc = CourseService(store=cs, kb_store=ks)
    svc.attach_library("course_a", "kb_x")
    svc.attach_library("course_b", "kb_x")
    a = cs.get_course("course_a")
    b = cs.get_course("course_b")
    assert a is not None and b is not None
    assert a.library_count == 0
    assert b.library_count == 1


def test_detach_library_does_not_delete_library(fresh) -> None:
    cs, ks = fresh
    cs.upsert_course(Course(id="course_a", name="A"))
    ks.upsert_library(KnowledgeBaseRecord(id="kb_x", name="X"))
    svc = CourseService(store=cs, kb_store=ks)
    svc.attach_library("course_a", "kb_x")
    svc.detach_library("course_a", "kb_x")
    lib = ks.get_library("kb_x")
    assert lib is not None
    assert lib.course_id is None
    course = cs.get_course("course_a")
    assert course is not None
    assert course.library_count == 0


def test_delete_course_detaches_libraries(fresh) -> None:
    """Deleting a course moves libraries out, does NOT delete them."""
    cs, ks = fresh
    cs.upsert_course(Course(id="course_a", name="A"))
    ks.upsert_library(KnowledgeBaseRecord(id="kb_x", name="X"))
    svc = CourseService(store=cs, kb_store=ks)
    svc.attach_library("course_a", "kb_x")
    assert svc.delete_course("course_a") is True
    # Course row is gone.
    assert cs.get_course("course_a") is None
    # Library still exists, with course_id NULL.
    lib = ks.get_library("kb_x")
    assert lib is not None
    assert lib.course_id is None


def test_attach_unknown_library_raises(fresh) -> None:
    cs, ks = fresh
    cs.upsert_course(Course(id="course_a", name="A"))
    svc = CourseService(store=cs, kb_store=ks)
    with pytest.raises(ValueError, match="library not found"):
        svc.attach_library("course_a", "kb_does_not_exist")


def test_attach_to_unknown_course_returns_none(fresh) -> None:
    cs, ks = fresh
    ks.upsert_library(KnowledgeBaseRecord(id="kb_x", name="X"))
    svc = CourseService(store=cs, kb_store=ks)
    assert svc.attach_library("course_does_not_exist", "kb_x") is None


def test_seed_default_courses_idempotent(fresh) -> None:
    cs, ks = fresh
    # Pre-create the AI introduction KB so the seed has something to attach.
    ks.upsert_library(
        KnowledgeBaseRecord(
            id="ai_introduction",
            name="AI 导论（预置）",
            description="预置课程",
            is_seeded=True,
        )
    )
    svc = CourseService(store=cs, kb_store=ks)
    seed_default_courses(svc)
    seed_default_courses(svc)  # idempotent
    course = cs.get_course("course_ai_intro")
    assert course is not None
    assert course.is_seeded is True
    assert course.knowledge_graph_id == "ai_introduction"
    lib = ks.get_library("ai_introduction")
    assert lib is not None
    assert lib.course_id == "course_ai_intro"
