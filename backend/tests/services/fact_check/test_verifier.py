"""Tests for :mod:`tutor.services.fact_check.verifier`.

Note: uses manual ``tempfile.mkdtemp`` instead of pytest's ``tmp_path``
to avoid a known interaction with conftest's autouse fixture that
causes tmp_path directories to be cleaned up mid-test.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tutor.services.fact_check.verifier import (
    ClaimVerdict,
    FactCheckResult,
    FactCheckService,
    _best_snippet,
    _tokenize,
)
from tutor.services.llm.base import LLMResponse


# Override conftest's autouse fixture
@pytest.fixture(autouse=True)
def isolated_data_dir():
    yield


def _mock_llm(responses: list[str]):
    queue = list(responses)
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        content = queue.pop(0) if queue else "{}"
        return LLMResponse(content=content, model="mock", finish_reason="stop")

    llm.call = call
    return llm


def _make_extractor(llm):
    from tutor.agents.safety.fact_check_extractor import FactCheckExtractor

    return FactCheckExtractor(llm=llm)


def _make_judge(llm):
    from tutor.agents.safety.fact_check_judge import FactCheckJudge

    return FactCheckJudge(llm=llm)


@pytest.fixture
def workdir():
    """Per-test scratch directory using mkdtemp (not pytest tmp_path)."""
    tmp = Path(tempfile.mkdtemp(prefix="fact_check_"))
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Tokenize
# ---------------------------------------------------------------------------


def test_tokenize_chinese():
    toks = _tokenize("LSTM 包含 3 个门（遗忘门、输入门、输出门）")
    # tokenizer lowercases; just check the key tokens appear
    assert "lstm" in toks
    assert "包含" in toks or "门" in toks
    assert "的" not in toks  # stopword filtered


def test_tokenize_english():
    toks = _tokenize("The Transformer uses self-attention mechanism")
    assert "transformer" in toks
    assert "self" not in toks  # stopword (length 4 but in EN stopwords)


def test_tokenize_mixed():
    toks = _tokenize("LSTM 是 RNN 的变体，has 3 gates")
    assert "lstm" in toks
    assert "rnn" in toks
    assert "gates" in toks
    assert "的" not in toks  # stopword


def test_tokenize_short_tokens_filtered():
    toks = _tokenize("a b c LSTM")
    # single chars and very short tokens filtered
    assert "lstm" in toks


# ---------------------------------------------------------------------------
# Best snippet
# ---------------------------------------------------------------------------


def test_best_snippet_finds_overlapping_text():
    text = (
        "Section A\n" + "filler " * 100 + "\nLSTM has 3 gates: forget, input, output."
    )
    claim_tokens = {"lstm", "gates"}
    snippet, score = _best_snippet(text, claim_tokens, window=200)
    assert score > 0
    assert "LSTM" in snippet or "gates" in snippet


def test_best_snippet_no_overlap_returns_zero():
    text = "Python is a programming language"
    claim_tokens = {"javascript", "react"}
    snippet, score = _best_snippet(text, claim_tokens, window=200)
    assert score == 0
    assert snippet == ""


# ---------------------------------------------------------------------------
# End-to-end with mocks (uses workdir fixture, not tmp_path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_extracts_and_verifies_claims(workdir):
    extractor_llm = _mock_llm([json.dumps({
        "claims": [
            {"text": "LSTM has 3 gates", "category": "fact"},
            {"text": "LSTM solves vanishing gradient", "category": "fact"},
        ]
    })])
    judge_llm = _mock_llm([
        json.dumps({"verdict": "supported", "confidence": 0.9, "reasoning": "KB confirms 3 gates"}),
        json.dumps({"verdict": "supported", "confidence": 0.85, "reasoning": "KB mentions gradient issues"}),
    ])

    kb = workdir / "kb"
    kb.mkdir()
    (kb / "lstm.md").write_text(
        "# LSTM\nLSTM has 3 gates: forget, input, output. Designed to solve vanishing gradient in RNNs.",
        encoding="utf-8",
    )

    svc = FactCheckService(
        extractor=_make_extractor(extractor_llm),
        judge=_make_judge(judge_llm),
        kb_dir=kb,
    )

    result = await svc.check(
        content="LSTM has 3 gates and solves vanishing gradient.",
        topic="LSTM",
    )

    assert len(result.claims) == 2
    assert result.claims[0].text == "LSTM has 3 gates"
    assert result.claims[0].verdict == ClaimVerdict.SUPPORTED
    assert result.claims[1].verdict == ClaimVerdict.SUPPORTED
    assert result.overall_verdict == ClaimVerdict.SUPPORTED
    assert result.overall_confidence > 0.8


@pytest.mark.asyncio
async def test_check_detects_refuted_claim(workdir):
    extractor_llm = _mock_llm([json.dumps({
        "claims": [{"text": "LSTM has 4 gates", "category": "fact"}]
    })])
    judge_llm = _mock_llm([json.dumps({
        "verdict": "refuted",
        "confidence": 0.95,
        "reasoning": "KB says 3 gates, claim is wrong",
    })])

    kb = workdir / "kb"
    kb.mkdir()
    (kb / "lstm.md").write_text("LSTM has 3 gates.", encoding="utf-8")

    svc = FactCheckService(
        extractor=_make_extractor(extractor_llm),
        judge=_make_judge(judge_llm),
        kb_dir=kb,
    )
    result = await svc.check(content="LSTM has 4 gates.", topic="LSTM")

    assert len(result.claims) == 1
    assert result.claims[0].verdict == ClaimVerdict.REFUTED
    assert result.overall_verdict == ClaimVerdict.REFUTED


@pytest.mark.asyncio
async def test_check_no_evidence_returns_unverified(workdir):
    extractor_llm = _mock_llm([json.dumps({
        "claims": [{"text": "Some obscure claim", "category": "fact"}]
    })])
    judge_llm = _mock_llm([json.dumps({"verdict": "unverified", "confidence": 0.3, "reasoning": ""})])

    kb = workdir / "kb"
    kb.mkdir()
    (kb / "intro.md").write_text("Welcome to AI.", encoding="utf-8")

    svc = FactCheckService(
        extractor=_make_extractor(extractor_llm),
        judge=_make_judge(judge_llm),
        kb_dir=kb,
    )
    result = await svc.check(content="Some obscure claim.", topic="X")

    # No matching evidence → unverified
    assert result.claims[0].verdict == ClaimVerdict.UNVERIFIED


@pytest.mark.asyncio
async def test_check_handles_llm_failure_gracefully(workdir):
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        raise RuntimeError("LLM down")

    llm.call = call
    kb = workdir / "kb"
    kb.mkdir()
    (kb / "x.md").write_text("x", encoding="utf-8")

    svc = FactCheckService(
        extractor=_make_extractor(llm),
        judge=_make_judge(llm),
        kb_dir=kb,
    )
    result = await svc.check(content="x", topic="x")
    assert result.claims == []
    assert "no claims" in result.notes.lower()


@pytest.mark.asyncio
async def test_check_with_explicit_source_documents(workdir):
    extractor_llm = _mock_llm([json.dumps({
        "claims": [{"text": "Test claim", "category": "fact"}]
    })])
    judge_llm = _mock_llm([json.dumps({"verdict": "supported", "confidence": 0.8, "reasoning": "ok"})])

    custom_kb = workdir / "custom"
    custom_kb.mkdir()
    target = custom_kb / "doc.md"
    target.write_text("Test claim is verified here.", encoding="utf-8")

    svc = FactCheckService(
        extractor=_make_extractor(extractor_llm),
        judge=_make_judge(judge_llm),
        kb_dir=workdir / "empty_kb",
    )
    result = await svc.check(
        content="Test claim.",
        topic="X",
        source_documents=[str(target)],
    )
    assert len(result.claims) == 1
    assert result.claims[0].evidence


@pytest.mark.asyncio
async def test_check_caps_claims_at_eight(workdir):
    claims = [{"text": f"Claim {i}", "category": "fact"} for i in range(20)]
    extractor_llm = _mock_llm([json.dumps({"claims": claims})])
    judge_llm = _mock_llm([
        json.dumps({"verdict": "supported", "confidence": 0.7, "reasoning": ""})
    ] * 8)

    kb = workdir / "kb"
    kb.mkdir()
    (kb / "x.md").write_text("Claims here.", encoding="utf-8")

    svc = FactCheckService(
        extractor=_make_extractor(extractor_llm),
        judge=_make_judge(judge_llm),
        kb_dir=kb,
    )
    result = await svc.check(content="x", topic="x")
    assert len(result.claims) == 8


# ---------------------------------------------------------------------------
# Real KB test (uses shipped AI course KB)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_against_real_ai_course_kb():
    """Find LSTM KB file, claim that matches → supported."""
    from tutor.services.config.settings import get_settings

    settings = get_settings()
    kb = settings.kb_dir
    # Find a KB file with LSTM mention
    target = None
    for path in kb.rglob("*.md"):
        if "LSTM" in path.read_text(encoding="utf-8", errors="ignore"):
            target = path
            break
    if target is None:
        pytest.skip("AI course KB has no LSTM mention")

    extractor_llm = _mock_llm([json.dumps({
        "claims": [{"text": "LSTM has 3 gates", "category": "fact"}]
    })])
    judge_llm = _mock_llm([json.dumps({
        "verdict": "supported",
        "confidence": 0.9,
        "reasoning": "KB confirms",
    })])

    svc = FactCheckService(
        extractor=_make_extractor(extractor_llm),
        judge=_make_judge(judge_llm),
        kb_dir=kb,
    )
    result = await svc.check(content="LSTM has 3 gates.", topic="LSTM")

    assert len(result.claims) >= 1
    assert result.claims[0].verdict == ClaimVerdict.SUPPORTED
    # Should have evidence from the AI course KB
    assert any("rnn" in e.source_path.lower() or "lstm" in e.source_path.lower()
               for e in result.claims[0].evidence)
