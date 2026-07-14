"""Agent implementations for TutorBot.

Each Agent encapsulates a single domain-responsible LLM call pattern.
Agents inherit from :class:`tutor.agents.base_agent.BaseAgent` and
implement :meth:`process`.

Subpackages:
- :mod:`tutor.agents.profile`    — Learner profile construction
- :mod:`tutor.agents.resource`   — Resource generation cluster
- :mod:`tutor.agents.path`       — Learning path planning
- :mod:`tutor.agents.tutor`      — Tutoring (Q&A)
- :mod:`tutor.agents.assessment` — Learning assessment
- :mod:`tutor.agents.safety`     — Anti-hallucination, content safety
"""

from tutor.agents.base_agent import BaseAgent, TraceCallback

__all__ = ["BaseAgent", "TraceCallback"]
