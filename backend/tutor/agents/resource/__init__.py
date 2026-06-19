"""Resource generation agent cluster (核心 — 7 agents).

Cluster composition (per idea.md):

- :class:`ContentExpertAgent`     — knowledge accuracy, RAG-backed initial content
- :class:`PedagogyAgent`         — restructure for teaching (examples, key points)
- :class:`ExerciseGeneratorAgent`— tiered exercises (basic/advanced/challenge)
- :class:`MultimediaAgent`       — mind maps, comparison tables (Mermaid)
- :class:`ManimVideoAgent`       — two-stage concept-designer → Manim code
- :class:`CodeSandboxAgent`      — code examples with sandbox verification
- :class:`QualityReviewerAgent`  — review & verdict (pass/revise/reject)
"""

from tutor.agents.resource.code_sandbox import CodeSandboxAgent
from tutor.agents.resource.content_expert import (
    CONTENT_OUTPUT_SCHEMA,
    ContentExpertAgent,
)
from tutor.agents.resource.exercise_generator import (
    EXERCISE_OUTPUT_SCHEMA,
    ExerciseGeneratorAgent,
)
from tutor.agents.resource.manim_video import (
    STORYBOARD_SCHEMA,
    ManimVideoAgent,
)
from tutor.agents.resource.multimedia import (
    MINDMAP_OUTPUT_SCHEMA,
    MultimediaAgent,
)
from tutor.agents.resource.pedagogy import (
    PEDAGOGY_OUTPUT_SCHEMA,
    PedagogyAgent,
)
from tutor.agents.resource.quality_reviewer import (
    REVIEW_OUTPUT_SCHEMA,
    QualityReviewerAgent,
)

__all__ = [
    "CODE_OUTPUT_SCHEMA",
    "CONTENT_OUTPUT_SCHEMA",
    "EXERCISE_OUTPUT_SCHEMA",
    "MINDMAP_OUTPUT_SCHEMA",
    "PEDAGOGY_OUTPUT_SCHEMA",
    "REVIEW_OUTPUT_SCHEMA",
    "STORYBOARD_SCHEMA",
    "CodeSandboxAgent",
    "ContentExpertAgent",
    "ExerciseGeneratorAgent",
    "ManimVideoAgent",
    "MultimediaAgent",
    "PedagogyAgent",
    "QualityReviewerAgent",
]
