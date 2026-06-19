"""Tests for TutorService."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import pytest

from tutor.agents.tutor.question_understanding import (
    QuestionType,
    QuestionUnderstanding,
)
from tutor.agents.tutor.tutoring import TutoringAnswer
from tutor.services.tutor.service import (
    TutorService,
    TutorTurn,
    _tokenize,
    get_tutor_service,
    reset_tutor_service,
)


@pytest.fixture
def workdir():
    tmp = Path(tempfile.mkdtemp(prefix="tutor_service_"))
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def fresh_service(workdir):
    return TutorService(kb_dir=workdir)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def test_get_session_creates_if_missing(fresh_service):
    sess = fresh_service.get_session("alice")
    assert sess.user_id == "alice"
    assert sess.turns == []


def test_record_and_get_history(fresh_service):
    und = QuestionUnderstanding(raw_question="x")
    ans = TutoringAnswer(tldr="x")

    fresh_service.record_interaction(
        user_id="alice",
        question="Q1",
        understanding=und,
        answer=ans,
    )
    fresh_service.record_interaction(
        user_id="alice",
        question="Q2",
        understanding=und,
        answer=ans,
    )

    history = fresh_service.get_history("alice", limit=10)
    assert len(history) == 2
    assert history[0].question == "Q1"
    assert history[1].question == "Q2"


def test_history_is_per_user(fresh_service):
    und = QuestionUnderstanding(raw_question="x")
    ans = TutoringAnswer(tldr="x")

    fresh_service.record_interaction(
        user_id="alice", question="A1", understanding=und, answer=ans
    )
    fresh_service.record_interaction(
        user_id="bob", question="B1", understanding=und, answer=ans
    )

    assert len(fresh_service.get_history("alice")) == 1
    assert len(fresh_service.get_history("bob")) == 1


def test_history_caps_at_max(fresh_service):
    fresh_service.max_history_per_user = 3
    und = QuestionUnderstanding(raw_question="x")
    ans = TutoringAnswer(tldr="x")
    for i in range(10):
        fresh_service.record_interaction(
            user_id="u", question=f"Q{i}", understanding=und, answer=ans
        )
    history = fresh_service.get_history("u")
    assert len(history) == 3
    # Most recent kept
    assert history[-1].question == "Q9"


def test_common_concepts(fresh_service):
    for _ in range(3):
        und = QuestionUnderstanding(
            raw_question="x", concepts=["LSTM", "RNN"]
        )
        ans = TutoringAnswer(tldr="x")
        fresh_service.record_interaction(
            user_id="u", question="x", understanding=und, answer=ans
        )
    und2 = QuestionUnderstanding(
        raw_question="x", concepts=["Transformer"]
    )
    fresh_service.record_interaction(
        user_id="u", question="x", understanding=und2, answer=ans
    )

    sess = fresh_service.get_session("u")
    common = sess.common_concepts(top_k=5)
    # LSTM appears 3 times (most common)
    assert common[0] == ("LSTM", 3)
    assert ("RNN", 3) in common
    assert ("Transformer", 1) in common


def test_reset_specific_user(fresh_service):
    und = QuestionUnderstanding(raw_question="x")
    ans = TutoringAnswer(tldr="x")
    fresh_service.record_interaction(
        user_id="alice", question="x", understanding=und, answer=ans
    )
    fresh_service.record_interaction(
        user_id="bob", question="x", understanding=und, answer=ans
    )
    fresh_service.reset("alice")
    assert fresh_service.get_history("alice") == []
    assert len(fresh_service.get_history("bob")) == 1


def test_reset_all(fresh_service):
    und = QuestionUnderstanding(raw_question="x")
    ans = TutoringAnswer(tldr="x")
    fresh_service.record_interaction(
        user_id="alice", question="x", understanding=und, answer=ans
    )
    fresh_service.reset()
    assert fresh_service.get_history("alice") == []


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def _write_kb(workdir: Path, files: dict[str, str]):
    workdir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (workdir / name).write_text(content, encoding="utf-8")


@pytest.mark.asyncio
async def test_retrieve_context_finds_overlapping_kb(workdir):
    _write_kb(workdir, {
        "lstm.md": "LSTM has 3 gates: forget, input, output. Used for sequence modeling.",
        "other.md": "Python is a programming language.",
    })
    svc = TutorService(kb_dir=workdir)
    ctx = await svc.retrieve_context(
        question="LSTM gates",
        concepts=["LSTM"],
    )
    # Should find lstm.md, not other.md
    assert "lstm.md" in ctx
    assert "3 gates" in ctx
    assert "Python is a programming language" not in ctx


@pytest.mark.asyncio
async def test_retrieve_context_concepts_weighted_higher(workdir):
    _write_kb(workdir, {
        "a.md": "LSTM is mentioned here once.",
        "b.md": "LSTM appears LSTM LSTM LSTM multiple times.",
    })
    svc = TutorService(kb_dir=workdir)
    ctx = await svc.retrieve_context(
        question="neural network",
        concepts=["LSTM"],
    )
    # b.md should rank higher because of more concept overlap
    assert "b.md" in ctx


@pytest.mark.asyncio
async def test_retrieve_context_no_matches(workdir):
    _write_kb(workdir, {
        "x.md": "completely unrelated content",
    })
    svc = TutorService(kb_dir=workdir)
    ctx = await svc.retrieve_context(
        question="quantum mechanics",
        concepts=["physics"],
    )
    # No overlap → empty
    assert ctx == ""


@pytest.mark.asyncio
async def test_retrieve_context_explicit_documents(workdir):
    _write_kb(workdir, {
        "ignored.md": "Python is unrelated.",
        "explicit.md": "LSTM has 3 gates.",
    })
    svc = TutorService(kb_dir=workdir)
    ctx = await svc.retrieve_context(
        question="LSTM",
        concepts=["LSTM"],
        source_documents=[str(workdir / "explicit.md")],
    )
    assert "explicit.md" in ctx
    assert "ignored.md" not in ctx


@pytest.mark.asyncio
async def test_retrieve_context_caps_at_top_k(workdir):
    files = {f"f{i}.md": f"LSTM mentioned in file {i}" for i in range(10)}
    _write_kb(workdir, files)
    svc = TutorService(kb_dir=workdir, retrieval_top_k=3)
    ctx = await svc.retrieve_context(
        question="LSTM", concepts=["LSTM"]
    )
    # Should only include top 3
    assert ctx.count("### [") == 3


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


def test_tokenize_filters_stopwords():
    toks = _tokenize("the LSTM is great")
    assert "the" not in toks
    assert "is" not in toks
    assert "lstm" in toks
    assert "great" in toks


def test_tokenize_chinese():
    toks = _tokenize("LSTM 包含 3 个门")
    assert "包含" in toks or "门" in toks
    assert "的" not in toks


def test_tokenize_handles_empty():
    assert _tokenize("") == []
    assert _tokenize(None) == []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_get_tutor_service_singleton():
    reset_tutor_service()
    svc1 = get_tutor_service()
    svc2 = get_tutor_service()
    assert svc1 is svc2
    reset_tutor_service()


def test_reset_tutor_service():
    svc = get_tutor_service()
    svc.reset()
    assert svc.get_history("any") == []


# ---------------------------------------------------------------------------
# TutorTurn
# ---------------------------------------------------------------------------


def test_tutor_turn_to_dict():
    und = QuestionUnderstanding(
        question_type=QuestionType.CONCEPT,
        raw_question="What is X?",
    )
    ans = TutoringAnswer(tldr="X is ...")
    turn = TutorTurn(
        user_id="alice",
        question="What is X?",
        understanding=und,
        answer=ans,
    )
    d = turn.to_dict()
    assert d["user_id"] == "alice"
    assert d["question"] == "What is X?"
    assert d["understanding"]["question_type"] == "concept"
    assert d["answer"]["tldr"] == "X is ..."
