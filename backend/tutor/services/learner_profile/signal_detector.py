"""Heuristic gate: does a learner message carry profile-building signal?

Pure, cheap and deterministic — the LLM feature extractor only runs when
this returns True. Three pattern families from the approved design
(docs/superpowers/specs/2026-07-19-conversational-profile-building-design.md):
identity/major (strong), learning goal (weak), learning history (weak).
"""

from __future__ import annotations

import re

_STRONG_IDENTITY = re.compile(
    r"我是|我现在是|我就读|我的专业|专业是|研[一二三]|大[一二三四]"
    r"|本科生|硕士生|博士生|i'?m a|i am a|my major|i study",
    re.IGNORECASE,
)
_WEAK_GOAL = re.compile(
    r"我想学|我要学|我想了解|目标是|打算学|准备(考试|面试|考研|求职|期末)"
    r"|i want to learn|my goal",
    re.IGNORECASE,
)
_WEAK_HISTORY = re.compile(
    r"之前学过|以前学过|没学过|零基础|有[^，。,.]{0,6}基础|不太熟|比较熟"
    r"|熟悉|了解过|自学过|i'?ve studied|new to|familiar with",
    re.IGNORECASE,
)


def detect_profile_signal(message: str, *, has_profile: bool) -> bool:
    """Return True when `message` is worth an LLM profile-extraction call."""
    text = (message or "").strip()
    if not text:
        return False
    if _STRONG_IDENTITY.search(text):
        return True
    goal = bool(_WEAK_GOAL.search(text))
    history = bool(_WEAK_HISTORY.search(text))
    if goal and history:
        return True
    if not has_profile and (goal or history):
        return True
    return False


__all__ = ["detect_profile_signal"]
