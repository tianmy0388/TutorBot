"""PPT generation service (Phase 5.3).

Wraps ``python-pptx`` to convert Markdown source into a slide deck.
Exposed to the resource_generation pipeline through
:class:`tutor.agents.resource.ppt_generator.PPTGeneratorAgent`.
"""

from tutor.services.ppt.service import (
    PPTGenerationService,
    Slide,
    get_ppt_service,
    render_slides,
    slice_markdown_to_slides,
)

__all__ = [
    "PPTGenerationService",
    "Slide",
    "get_ppt_service",
    "render_slides",
    "slice_markdown_to_slides",
]