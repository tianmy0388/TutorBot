"""Fact-check verifier: extract claims, retrieve evidence, judge consistency.

Public API
----------

    svc = get_fact_check_service()
    report = await svc.check(
        content="# LSTM\n\nLSTM has 3 gates...\n",
        topic="LSTM",
        source_documents=["path/to/lstm.md", ...],
    )

    for claim in report.claims:
        print(claim.text, "→", claim.verdict, claim.confidence)
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.services.config.settings import get_settings

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


class ClaimVerdict(str, Enum):  # noqa: UP042 - persisted enum compatibility
    """How a claim compares to retrieved evidence."""

    SUPPORTED = "supported"     # evidence confirms the claim
    REFUTED = "refuted"         # evidence contradicts the claim
    UNVERIFIED = "unverified"   # evidence is inconclusive


@dataclass
class FactEvidence:
    """One piece of evidence for / against a claim."""

    source_path: str  # path of KB file
    snippet: str     # excerpt that matched
    score: float     # relevance score (0-1)


@dataclass
class ClaimCheck:
    """A single claim's verification result."""

    text: str
    verdict: ClaimVerdict = ClaimVerdict.UNVERIFIED
    confidence: float = 0.5  # 0-1
    reasoning: str = ""
    evidence: list[FactEvidence] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "verdict": self.verdict.value,
            "confidence": round(self.confidence, 3),
            "reasoning": self.reasoning,
            "evidence_count": len(self.evidence),
        }


@dataclass
class FactCheckResult:
    """Full report from one fact-check run."""

    topic: str
    claims: list[ClaimCheck] = field(default_factory=list)
    overall_confidence: float = 0.5
    overall_verdict: ClaimVerdict = ClaimVerdict.UNVERIFIED
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "claim_count": len(self.claims),
            "supported_count": sum(
                1 for c in self.claims if c.verdict == ClaimVerdict.SUPPORTED
            ),
            "refuted_count": sum(
                1 for c in self.claims if c.verdict == ClaimVerdict.REFUTED
            ),
            "unverified_count": sum(
                1 for c in self.claims if c.verdict == ClaimVerdict.UNVERIFIED
            ),
            "overall_confidence": round(self.overall_confidence, 3),
            "overall_verdict": self.overall_verdict.value,
            "notes": self.notes,
            "claims": [c.to_dict() for c in self.claims],
        }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


# Common Chinese stopwords — kept tiny for MVP
_STOPWORDS_ZH = {"的", "了", "在", "是", "和", "与", "及", "或", "也", "但", "而", "等", "这", "那", "我", "你", "他", "她", "它", "我们", "你们", "他们", "一个", "一些", "这个", "那个", "这种", "那种", "是的", "不是", "可以", "可能", "应该"}
_STOPWORDS_EN = {"the", "a", "an", "and", "or", "but", "if", "is", "are", "was", "were", "be", "been", "have", "has", "do", "does", "this", "that", "these", "those", "i", "you", "he", "she", "it", "we", "they", "self"}


class FactCheckService:
    """End-to-end claim → evidence → verdict pipeline."""

    def __init__(
        self,
        *,
        extractor: BaseAgent | None = None,
        judge: BaseAgent | None = None,
        kb_dir: Path | None = None,
    ) -> None:
        # Lazy import to avoid circular dependency
        from tutor.agents.safety.fact_check_extractor import FactCheckExtractor
        from tutor.agents.safety.fact_check_judge import FactCheckJudge

        self.extractor = extractor or FactCheckExtractor()
        self.judge = judge or FactCheckJudge()
        self.kb_dir = Path(kb_dir) if kb_dir else get_settings().kb_dir

    async def check(
        self,
        content: str,
        topic: str = "",
        source_documents: list[str] | None = None,
    ) -> FactCheckResult:
        """Full check pipeline."""
        # Stage 1: extract claims (LLM)
        claims = await self._extract_claims(content, topic)
        if not claims:
            return FactCheckResult(
                topic=topic,
                notes="no claims extracted",
                overall_verdict=ClaimVerdict.UNVERIFIED,
            )

        # Stage 2: for each claim, retrieve evidence
        enriched: list[ClaimCheck] = []
        for claim_text in claims:
            evidence = self._retrieve_evidence(claim_text, source_documents)
            check = ClaimCheck(
                text=claim_text,
                evidence=evidence,
            )
            enriched.append(check)

        # Stage 3: judge each claim against evidence (LLM)
        for check in enriched:
            await self._judge(check)

        # Aggregate
        supported = sum(1 for c in enriched if c.verdict == ClaimVerdict.SUPPORTED)
        refuted = sum(1 for c in enriched if c.verdict == ClaimVerdict.REFUTED)
        total = len(enriched)
        overall_conf = sum(c.confidence for c in enriched) / max(1, total)

        if refuted > 0:
            overall_verdict = ClaimVerdict.REFUTED
        elif supported == total:
            overall_verdict = ClaimVerdict.SUPPORTED
        else:
            overall_verdict = ClaimVerdict.UNVERIFIED

        notes = (
            f"{supported}/{total} claims supported, "
            f"{refuted}/{total} refuted, "
            f"{total - supported - refuted}/{total} unverified"
        )

        return FactCheckResult(
            topic=topic,
            claims=enriched,
            overall_confidence=overall_conf,
            overall_verdict=overall_verdict,
            notes=notes,
        )

    # ------------------------------------------------------------------
    # Claim extraction (LLM)
    # ------------------------------------------------------------------

    async def _extract_claims(self, content: str, topic: str) -> list[str]:
        """Use the LLM to pull 3-8 key factual claims from the content."""
        from tutor.core.context import UnifiedContext

        ctx = UnifiedContext(language="zh")
        try:
            return await self.extractor.process(
                ctx, content=content, topic=topic
            )
        except Exception:
            logger.warning("FACT_CHECK_EXTRACTION_FAILED")
            return []  # cap at 8 claims handled inside extractor

    # ------------------------------------------------------------------
    # Evidence retrieval (keyword search)
    # ------------------------------------------------------------------

    def _retrieve_evidence(
        self,
        claim: str,
        source_documents: list[str] | None = None,
    ) -> list[FactEvidence]:
        """Find KB snippets relevant to ``claim``.

        Uses simple token-overlap scoring. Returns top 3 snippets.
        """
        candidates: list[Path] = []
        if source_documents:
            candidates = [Path(p) for p in source_documents if Path(p).exists()]
        else:
            candidates = self._discover_kb_files()

        if not candidates:
            return []

        claim_tokens = _tokenize(claim)
        if not claim_tokens:
            return []

        scored: list[tuple[float, str, str]] = []
        for path in candidates:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            snippet, score = _best_snippet(text, claim_tokens, window=400)
            if score > 0:
                scored.append((score, str(path), snippet))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            FactEvidence(source_path=p, snippet=s, score=min(1.0, score / 10.0))
            for score, p, s in scored[:3]
        ]

    def _discover_kb_files(self) -> list[Path]:
        """Find all .md files under the configured KB directory."""
        out: list[Path] = []
        if not self.kb_dir.exists():
            return out
        for p in self.kb_dir.rglob("*.md"):
            if p.is_file():
                out.append(p)
        return out

    # ------------------------------------------------------------------
    # Judgement (LLM)
    # ------------------------------------------------------------------

    async def _judge(self, check: ClaimCheck) -> None:
        """Ask LLM to label ``check`` as supported / refuted / unverified."""
        if not check.evidence:
            check.verdict = ClaimVerdict.UNVERIFIED
            check.reasoning = "no evidence retrieved"
            check.confidence = 0.3
            return

        from tutor.core.context import UnifiedContext

        evidence_text = "\n\n---\n\n".join(
            f"[{e.source_path}]\n{e.snippet}" for e in check.evidence
        )

        try:
            ctx = UnifiedContext(language="zh")
            result = await self.judge.process(
                ctx, claim=check.text, evidence=evidence_text
            )
            check.verdict = result.verdict
            check.confidence = result.confidence
            check.reasoning = result.reasoning
        except Exception:
            logger.warning("FACT_CHECK_JUDGE_FAILED policy=unverified")
            check.verdict = ClaimVerdict.UNVERIFIED
            check.reasoning = "FACT_CHECK_JUDGE_FAILED: evidence judgement unavailable"
            check.confidence = 0.3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Cheap tokenizer — splits on whitespace + Chinese characters.

    Removes stopwords. Returns lowercase tokens.
    """
    # Split on non-word characters (keeps CJK characters)
    parts = re.findall(r"[A-Za-z]+|[一-鿿]+", text)
    out: list[str] = []
    for p in parts:
        p_low = p.lower()
        if len(p) < 2:
            continue
        if p_low in _STOPWORDS_EN or p in _STOPWORDS_ZH:
            continue
        out.append(p_low)
    return out


def _best_snippet(
    text: str, claim_tokens: list[str], window: int = 400
) -> tuple[str, float]:
    """Find the highest-scoring ``window``-char snippet around claim tokens.

    Returns ``(snippet, score)``. Score is raw overlap count.
    """
    if not claim_tokens:
        return "", 0.0

    claim_set = set(claim_tokens)
    text_lower = text.lower()

    best_score = 0
    best_start = 0
    # Slide a window
    step = max(50, window // 4)
    for start in range(0, max(1, len(text) - window), step):
        end = min(len(text), start + window)
        chunk = text_lower[start:end]
        chunk_tokens = _tokenize(chunk)
        # Score = sum of overlapping claim tokens
        score = sum(1 for t in chunk_tokens if t in claim_set)
        if score > best_score:
            best_score = score
            best_start = start

    if best_score == 0:
        return "", 0.0
    snippet = text[best_start : best_start + window].strip()
    return snippet, float(best_score)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


_service: FactCheckService | None = None
_service_lock = threading.Lock()


def get_fact_check_service() -> FactCheckService:
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = FactCheckService()
                logger.info("FactCheckService ready")
    return _service


def reset_fact_check_service() -> None:
    global _service
    _service = None


__all__ = [
    "ClaimCheck",
    "ClaimVerdict",
    "FactCheckResult",
    "FactCheckService",
    "FactEvidence",
    "get_fact_check_service",
    "reset_fact_check_service",
]
