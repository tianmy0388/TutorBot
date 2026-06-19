"""PromptManager — loads YAML prompts with multi-language fallback.

Directory layout (relative to package root)::

    tutor/prompts/<module>/<lang>/<agent_name>.yaml

    e.g. tutor/prompts/profile/zh/feature_extractor.yaml

A module is a logical grouping (``profile``, ``resource``, ``path``...).

Languages are tried in order: requested → "zh"/"cn" → "en" (or "en" → "zh" → "cn").

Design inspired by DeepTutor's :class:`PromptManager`.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

# Known module names. Extend as new modules are added.
MODULES: list[str] = [
    "profile",
    "resource",
    "path",
    "tutor",
    "assessment",
    "safety",
]

LANGUAGE_FALLBACKS: dict[str, list[str]] = {
    "zh": ["zh", "cn", "en"],
    "cn": ["cn", "zh", "en"],
    "en": ["en", "zh", "cn"],
}


def _package_prompts_dir() -> Path:
    """Locate the package's ``prompts/`` directory.

    Works both from source (``backend/tutor/services/prompt/manager.py``)
    and from an installed wheel.
    """
    # Walk up from this file: services/prompt/ → tutor/ → prompts/
    return Path(__file__).resolve().parents[2] / "prompts"


class PromptManager:
    """Thread-safe singleton that caches loaded YAML prompts."""

    _instance: "PromptManager | None" = None
    _lock = threading.Lock()

    def __new__(cls) -> "PromptManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._cache = {}
                cls._instance._prompts_dir = _package_prompts_dir()
            return cls._instance

    def __init__(self) -> None:
        # __new__ already initialised
        pass

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    def load_prompts(
        self,
        module_name: str,
        agent_name: str,
        language: str = "zh",
        subdirectory: str | None = None,
    ) -> dict[str, Any]:
        """Load a prompt file with language fallback.

        Returns an empty dict if no file is found (with a warning).
        """
        cache_key = (module_name, agent_name, language, subdirectory)
        if cache_key in self._cache:
            return self._cache[cache_key]

        fallbacks = LANGUAGE_FALLBACKS.get(language, [language, "en"])
        for lang in fallbacks:
            prompt_path = self._resolve(module_name, lang, agent_name, subdirectory)
            if prompt_path and prompt_path.exists():
                try:
                    with prompt_path.open("r", encoding="utf-8") as fh:
                        data = yaml.safe_load(fh) or {}
                    self._cache[cache_key] = data
                    return data
                except Exception as exc:
                    logger.error(f"Failed to load prompt {prompt_path}: {exc!r}")
                    continue
        logger.warning(
            f"No prompt file found for module={module_name} agent={agent_name} "
            f"language={language} (tried: {fallbacks})"
        )
        self._cache[cache_key] = {}
        return {}

    def get_prompt(
        self,
        prompts: dict[str, Any],
        section: str,
        field: str | None = None,
        fallback: str = "",
    ) -> str:
        """Look up a prompt string from a loaded prompt dict.

        Schema::

            section:
              field: "..."
            section:
              content: "..."
            section: "..."

        Parameters
        ----------
        section : str
            Top-level key.
        field : str, optional
            Nested key under ``section``. If omitted, ``content`` is tried.
        """
        if not isinstance(prompts, dict):
            return fallback
        section_data = prompts.get(section)
        if section_data is None:
            return fallback
        if isinstance(section_data, str):
            return section_data or fallback
        if isinstance(section_data, dict):
            if field:
                v = section_data.get(field)
                if isinstance(v, str):
                    return v or fallback
            v = section_data.get("content")
            if isinstance(v, str):
                return v or fallback
        return fallback

    # ------------------------------------------------------------------
    # Cache management
    # ------------------------------------------------------------------

    def clear_cache(self, module_name: str | None = None) -> None:
        if module_name is None:
            self._cache.clear()
            return
        self._cache = {k: v for k, v in self._cache.items() if k[0] != module_name}

    def reload_prompts(
        self,
        module_name: str,
        agent_name: str,
        language: str = "zh",
        subdirectory: str | None = None,
    ) -> dict[str, Any]:
        self.clear_cache(module_name)
        return self.load_prompts(module_name, agent_name, language, subdirectory)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _resolve(
        self,
        module_name: str,
        language: str,
        agent_name: str,
        subdirectory: str | None,
    ) -> Path | None:
        base = self._prompts_dir / module_name / language
        if subdirectory:
            base = base / subdirectory
        return base / f"{agent_name}.yaml"


@lru_cache(maxsize=1)
def get_prompt_manager() -> PromptManager:
    """Return the singleton :class:`PromptManager`."""
    return PromptManager()


__all__ = [
    "LANGUAGE_FALLBACKS",
    "MODULES",
    "PromptManager",
    "get_prompt_manager",
]
