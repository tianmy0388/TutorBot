"""Prompt management: load YAML, multi-language fallback."""

from tutor.services.prompt.manager import (
    LANGUAGE_FALLBACKS,
    MODULES,
    PromptManager,
    get_prompt_manager,
)

__all__ = [
    "LANGUAGE_FALLBACKS",
    "MODULES",
    "PromptManager",
    "get_prompt_manager",
]
