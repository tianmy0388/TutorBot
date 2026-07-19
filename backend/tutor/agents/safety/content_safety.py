"""ContentSafetyAgent — keyword blacklist + LLM safety classification.

Two-stage pipeline:

1. **Keyword scan** — fast O(n) check against a small blacklist of clearly
   harmful categories (politics, hate speech, violence, adult, etc.).
   If a hit is found, escalate immediately.

2. **LLM safety classification** — if no keyword hit, ask the LLM whether
   the content is safe for an educational audience. The LLM is conservative
   and only flags content that is clearly inappropriate.

Output: :class:`SafetyReport` with a boolean ``is_safe`` flag + reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.redaction import redact_text
from tutor.core.stream_bus import StreamBus
from tutor.services.llm.base import LLMMessage, LLMRequest

# ---------------------------------------------------------------------------
# Keyword blacklist (Chinese + English)
# ---------------------------------------------------------------------------

# Each entry: (category, list of trigger substrings)
# These are CLEAR violations of an educational product's content policy.
KEYWORD_BLACKLIST: list[tuple[str, list[str]]] = [
    ("hate_speech", ["种族灭绝", "种族清洗", "ethnic cleansing", "genocide"]),
    ("violence", ["杀人方法", "how to kill", "制造炸弹", "make a bomb"]),
    ("adult", ["色情", "pornography", "裸聊"]),
    ("drugs", ["合成毒品配方", "synthesize meth"]),
    ("political_extremism", ["恐怖袭击教程", "terror attack how-to"]),
    ("self_harm", ["自杀方法", "how to commit suicide"]),
]


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------


@dataclass
class SafetyReport:
    """Outcome of a content-safety check."""

    is_safe: bool = True
    category: str = ""  # category that triggered (if unsafe)
    matched_keywords: list[str] = field(default_factory=list)
    llm_verdict: str = ""  # raw LLM response ("safe" / "unsafe" / ...)
    llm_reason: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_safe": self.is_safe,
            "category": self.category,
            "matched_keywords": list(self.matched_keywords),
            "llm_verdict": self.llm_verdict,
            "llm_reason": redact_text(self.llm_reason[:500]),
            "notes": redact_text(self.notes),
        }


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


SAFETY_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["safe", "unsafe"]},
        "reason": {"type": "string"},
        "category": {"type": "string"},
    },
    "required": ["verdict"],
}


SAFETY_SYSTEM_PROMPT = """You are a content-safety classifier for an educational product.

Decide whether the given content is appropriate for university students.

Be CONSERVATIVE — flag content only if it's clearly inappropriate:
- Hate speech / slurs
- Instructions for violence / weapons / drugs
- Sexual / adult content
- Self-harm instructions
- PII (real personal data)
- Targeted harassment

DO NOT flag:
- Academic discussions of historical events (even dark ones)
- Code samples (even for security demos)
- Mild criticism or strong opinions
- Difficult or uncomfortable topics

Output JSON: {"verdict": "safe"|"unsafe", "reason": "...", "category": "..."}"""


class ContentSafetyAgent(BaseAgent):
    """Check content for safety issues (keyword + LLM)."""

    module_name = "safety"
    agent_name = "content_safety"
    default_temperature = 0.1
    default_max_tokens = 512

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
        *,
        content: str,
    ) -> SafetyReport:
        report = SafetyReport()

        # Stage 1: keyword blacklist
        matched = self._scan_keywords(content)
        if matched:
            report.is_safe = False
            report.matched_keywords = matched
            report.category = matched[0][0]  # first hit's category
            report.notes = f"keyword match: {report.category}"
            return report

        # Stage 2: LLM classification
        if not content.strip():
            report.notes = "empty content"
            return report

        try:
            report.llm_verdict, report.llm_reason, cat = await self._classify_llm(
                content
            )
            if report.llm_verdict == "unsafe":
                report.is_safe = False
                report.category = cat or "unspecified"
                report.notes = f"LLM flagged as unsafe: {report.category}"
        except Exception:  # noqa: BLE001
            logger.warning("CONTENT_SAFETY_CHECK_FAILED policy=failed_open")
            report.notes = "CONTENT_SAFETY_CHECK_FAILED: safety classification failed"

        return report

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _scan_keywords(self, content: str) -> list[tuple[str, str]]:
        """Return [(category, keyword)] for any blacklist hits."""
        lower = content.lower()
        out: list[tuple[str, str]] = []
        for category, keywords in KEYWORD_BLACKLIST:
            for kw in keywords:
                if kw.lower() in lower:
                    out.append((category, kw))
        return out

    async def _classify_llm(
        self, content: str
    ) -> tuple[str, str, str]:
        """Return (verdict, reason, category) via LLM."""
        messages = [
            LLMMessage(role="system", content=SAFETY_SYSTEM_PROMPT),
            LLMMessage(
                role="user",
                content=(
                    f"Content to classify:\n```\n{content[:4000]}\n```\n\n"
                    f"Return JSON."
                ),
            ),
        ]
        request = LLMRequest(
            messages=messages,
            temperature=0.1,
            max_tokens=512,
            extra={"response_format": {"type": "json_object"}},
        )
        resp = await self.resolved_llm.call(request)
        data = self.parse_json_response(resp.content, fallback={})
        if not isinstance(data, dict):
            return "safe", "could not parse LLM response", ""
        verdict = str(data.get("verdict") or "safe").lower()
        reason = str(data.get("reason") or "")
        category = str(data.get("category") or "")
        return verdict, reason, category


__all__ = ["ContentSafetyAgent", "SafetyReport", "KEYWORD_BLACKLIST"]
