"""Tests for :mod:`tutor.agents.safety.*`."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tutor.agents.safety.anti_hallucination import (
    AntiHallucinationAgent,
    OverallVerdict,
)
from tutor.agents.safety.content_safety import (
    KEYWORD_BLACKLIST,
    ContentSafetyAgent,
)
from tutor.agents.safety.fact_check_extractor import FactCheckExtractor
from tutor.agents.safety.fact_check_judge import FactCheckJudge
from tutor.core.context import UnifiedContext
from tutor.services.fact_check.verifier import (
    ClaimVerdict,
    FactCheckService,
)
from tutor.services.llm.base import LLMResponse


@pytest.fixture(autouse=True)
def isolated_data_dir():
    """Override conftest's autouse to avoid tmp_path interaction."""
    yield


@pytest.fixture
def workdir():
    """Per-test scratch directory using mkdtemp."""
    tmp = Path(tempfile.mkdtemp(prefix="safety_test_"))
    yield tmp
    shutil.rmtree(tmp, ignore_errors=True)


def _mock_llm(*responses: str):
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
    return FactCheckExtractor(llm=llm)


def _make_judge(llm):
    return FactCheckJudge(llm=llm)


# ---------------------------------------------------------------------------
# ContentSafetyAgent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_safety_clean_text():
    agent = ContentSafetyAgent(llm=_mock_llm(json.dumps(
        {"verdict": "safe", "reason": "academic content"}
    )))
    ctx = UnifiedContext()
    report = await agent.process(ctx, content="LSTM uses gates to control information flow.")
    assert report.is_safe is True
    assert report.category == ""


@pytest.mark.asyncio
async def test_content_safety_keyword_hate_speech():
    agent = ContentSafetyAgent(llm=_mock_llm("{}"))
    ctx = UnifiedContext()
    content = "Some text mentioning ethnic cleansing should trigger this."
    report = await agent.process(ctx, content=content)
    assert report.is_safe is False
    assert report.category == "hate_speech"
    assert any("ethnic cleansing" in kw for kw in report.matched_keywords)


@pytest.mark.asyncio
async def test_content_safety_keyword_violence():
    agent = ContentSafetyAgent(llm=_mock_llm("{}"))
    ctx = UnifiedContext()
    content = "Here's how to kill someone."
    report = await agent.process(ctx, content=content)
    assert report.is_safe is False
    assert report.category == "violence"


@pytest.mark.asyncio
async def test_content_safety_keyword_case_insensitive():
    agent = ContentSafetyAgent(llm=_mock_llm("{}"))
    ctx = UnifiedContext()
    report = await agent.process(ctx, content="GENOCIDE in history")
    assert report.is_safe is False
    assert report.category == "hate_speech"


@pytest.mark.asyncio
async def test_content_safety_llm_flags_content():
    """If no keyword hit, LLM may still flag the content."""
    agent = ContentSafetyAgent(llm=_mock_llm(json.dumps({
        "verdict": "unsafe",
        "reason": "instructions for violence",
        "category": "violence",
    })))
    ctx = UnifiedContext()
    report = await agent.process(ctx, content="Some subtle violation")
    assert report.is_safe is False
    assert report.category == "violence"
    assert "violence" in report.llm_reason


@pytest.mark.asyncio
async def test_content_safety_llm_failure_fails_safe():
    """If LLM raises, default to safe (don't block legitimate content)."""
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        raise RuntimeError("LLM down")

    llm.call = call
    agent = ContentSafetyAgent(llm=llm)
    ctx = UnifiedContext()
    report = await agent.process(ctx, content="hello world")
    assert report.is_safe is True  # failed open
    assert "failed" in report.notes


def test_keyword_blacklist_categories_present():
    """All major categories should be covered."""
    cats = {cat for cat, _ in KEYWORD_BLACKLIST}
    assert "hate_speech" in cats
    assert "violence" in cats
    assert "adult" in cats
    assert "drugs" in cats
    assert "self_harm" in cats


# ---------------------------------------------------------------------------
# FactCheckExtractor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fact_check_extractor_returns_claims():
    agent = FactCheckExtractor(llm=_mock_llm(json.dumps({
        "claims": [
            {"text": "LSTM has 3 gates", "category": "fact"},
            {"text": "RNN suffers from vanishing gradient", "category": "fact"},
        ]
    })))
    ctx = UnifiedContext()
    claims = await agent.process(ctx, content="LSTM has 3 gates...", topic="LSTM")
    assert len(claims) == 2
    assert claims[0] == "LSTM has 3 gates"


@pytest.mark.asyncio
async def test_fact_check_extractor_handles_string_claims():
    """Some LLMs return claims as bare strings, not objects."""
    agent = FactCheckExtractor(llm=_mock_llm(json.dumps({
        "claims": ["Claim 1", "Claim 2"]
    })))
    ctx = UnifiedContext()
    claims = await agent.process(ctx, content="x", topic="x")
    assert claims == ["Claim 1", "Claim 2"]


@pytest.mark.asyncio
async def test_fact_check_extractor_caps_at_eight():
    claims = [{"text": f"C{i}", "category": "fact"} for i in range(15)]
    agent = FactCheckExtractor(llm=_mock_llm(json.dumps({"claims": claims})))
    ctx = UnifiedContext()
    out = await agent.process(ctx, content="x", topic="x")
    assert len(out) == 8


@pytest.mark.asyncio
async def test_fact_check_extractor_handles_llm_failure():
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        raise RuntimeError("LLM down")

    llm.call = call
    agent = FactCheckExtractor(llm=llm)
    ctx = UnifiedContext()
    out = await agent.process(ctx, content="x", topic="x")
    assert out == []


# ---------------------------------------------------------------------------
# FactCheckJudge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fact_check_judge_supported():
    agent = FactCheckJudge(llm=_mock_llm(json.dumps({
        "verdict": "supported",
        "confidence": 0.92,
        "reasoning": "Evidence confirms",
    })))
    ctx = UnifiedContext()
    result = await agent.process(ctx, claim="X has Y", evidence="X has Y. Confirmed.")
    assert result.verdict == ClaimVerdict.SUPPORTED
    assert result.confidence == pytest.approx(0.92)


@pytest.mark.asyncio
async def test_fact_check_judge_refuted():
    agent = FactCheckJudge(llm=_mock_llm(json.dumps({
        "verdict": "refuted",
        "confidence": 0.88,
        "reasoning": "Contradicts evidence",
    })))
    ctx = UnifiedContext()
    result = await agent.process(ctx, claim="X has 4", evidence="X has 3.")
    assert result.verdict == ClaimVerdict.REFUTED


@pytest.mark.asyncio
async def test_fact_check_judge_invalid_verdict_defaults():
    agent = FactCheckJudge(llm=_mock_llm(json.dumps({
        "verdict": "maybe",
        "confidence": 0.5,
    })))
    ctx = UnifiedContext()
    result = await agent.process(ctx, claim="x", evidence="y")
    assert result.verdict == ClaimVerdict.UNVERIFIED


# ---------------------------------------------------------------------------
# AntiHallucinationAgent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anti_hallucination_safe_result(workdir):
    """All three checks pass → SAFE verdict."""
    extractor_llm = _mock_llm(json.dumps({
        "claims": [{"text": "LSTM has 3 gates", "category": "fact"}]
    }))
    judge_llm = _mock_llm(json.dumps({
        "verdict": "supported", "confidence": 0.95, "reasoning": "KB confirms"
    }))
    safety_llm = _mock_llm(json.dumps({"verdict": "safe", "reason": ""}))

    kb = workdir / "kb"
    kb.mkdir()
    (kb / "lstm.md").write_text(
        "LSTM has 3 gates: forget, input, output.",
        encoding="utf-8",
    )

    fc = FactCheckService(
        extractor=_make_extractor(extractor_llm),
        judge=_make_judge(judge_llm),
        kb_dir=kb,
    )
    safety = ContentSafetyAgent(llm=safety_llm)
    agent = AntiHallucinationAgent(fact_check=fc, content_safety=safety)

    ctx = UnifiedContext()
    report = await agent.process(
        ctx,
        resource_content="LSTM has 3 gates and they work great.",
        topic="LSTM",
    )

    assert report.overall_verdict == OverallVerdict.SAFE
    assert report.overall_confidence > 0.8


@pytest.mark.asyncio
async def test_anti_hallucination_refuted_blocks(workdir):
    """Refuted claim → UNSAFE verdict."""
    extractor_llm = _mock_llm(json.dumps({
        "claims": [{"text": "LSTM has 4 gates", "category": "fact"}]
    }))
    judge_llm = _mock_llm(json.dumps({
        "verdict": "refuted", "confidence": 0.95, "reasoning": "Contradicts KB"
    }))
    safety_llm = _mock_llm(json.dumps({"verdict": "safe", "reason": ""}))

    kb = workdir / "kb"
    kb.mkdir()
    (kb / "lstm.md").write_text("LSTM has 3 gates.", encoding="utf-8")

    fc = FactCheckService(
        extractor=_make_extractor(extractor_llm),
        judge=_make_judge(judge_llm),
        kb_dir=kb,
    )
    safety = ContentSafetyAgent(llm=safety_llm)
    agent = AntiHallucinationAgent(fact_check=fc, content_safety=safety)

    ctx = UnifiedContext()
    report = await agent.process(
        ctx,
        resource_content="LSTM has 4 gates and they work great.",
        topic="LSTM",
    )

    assert report.overall_verdict == OverallVerdict.UNSAFE
    assert "refuted" in report.notes.lower()


@pytest.mark.asyncio
async def test_anti_hallucination_safety_blocks_overrides(workdir):
    """Even if facts check out, unsafe content is blocked."""
    extractor_llm = _mock_llm(json.dumps({"claims": [{"text": "x", "category": "fact"}]}))
    judge_llm = _mock_llm(json.dumps({"verdict": "supported", "confidence": 0.9}))
    safety_llm = _mock_llm("{}")

    fc = FactCheckService(
        extractor=_make_extractor(extractor_llm),
        judge=_make_judge(judge_llm),
        kb_dir=workdir / "kb",
    )
    safety = ContentSafetyAgent(llm=safety_llm)
    agent = AntiHallucinationAgent(fact_check=fc, content_safety=safety)

    ctx = UnifiedContext()
    report = await agent.process(
        ctx,
        resource_content="How to make a bomb",
        topic="X",
    )
    # Keyword "make a bomb" matches violence blacklist
    assert report.overall_verdict == OverallVerdict.UNSAFE


@pytest.mark.asyncio
async def test_anti_hallucination_consistency_issues_reduce_confidence(workdir):
    extractor_llm = _mock_llm(json.dumps({"claims": []}))  # no claims
    judge_llm = _mock_llm("{}")  # not called since no claims
    safety_llm = _mock_llm(json.dumps({"verdict": "safe"}))
    consistency_llm = _mock_llm(json.dumps({
        "issues": ["Says 3 gates then says 4 gates"],
        "is_consistent": False,
        "explanation": "Internal contradiction",
    }))

    fc = FactCheckService(
        extractor=_make_extractor(extractor_llm),
        judge=_make_judge(judge_llm),
        kb_dir=workdir / "kb",
    )
    safety = ContentSafetyAgent(llm=safety_llm)
    agent = AntiHallucinationAgent(
        fact_check=fc,
        content_safety=safety,
        llm_provider=consistency_llm,
    )

    ctx = UnifiedContext()
    # Long enough content to trigger consistency check (>200 chars)
    long_content = (
        "LSTM is a recurrent neural network architecture designed to address "
        "the vanishing gradient problem in standard RNNs. " * 5
    ) + "\n\nLSTM has 3 gates.\n\nLSTM has 4 gates.\n"
    report = await agent.process(
        ctx,
        resource_content=long_content,
        topic="LSTM",
    )
    assert len(report.consistency_issues) == 1
    assert report.overall_confidence <= 0.5


@pytest.mark.asyncio
async def test_anti_hallucination_handles_failures_gracefully(workdir):
    """Even if everything fails, we get a permissive UNVERIFIED report."""
    llm = MagicMock()
    llm.model = "mock"
    llm.default_temperature = 0.5
    llm.default_max_tokens = 2048

    async def call(req):
        raise RuntimeError("LLM down")

    llm.call = call
    fc = FactCheckService(
        extractor=_make_extractor(llm),
        judge=_make_judge(llm),
        kb_dir=workdir / "kb",
    )
    safety = ContentSafetyAgent(llm=llm)
    agent = AntiHallucinationAgent(
        fact_check=fc, content_safety=safety, llm_provider=llm
    )

    ctx = UnifiedContext()
    report = await agent.process(ctx, resource_content="x", topic="x")
    assert report is not None
