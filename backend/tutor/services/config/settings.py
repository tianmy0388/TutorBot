"""Centralised application settings, loaded from environment variables.

We use :mod:`pydantic_settings` so values are typed, validated, and
introspectable. The :func:`get_settings` helper caches a singleton
process-wide.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration.

    All values are populated from environment variables (with a ``TUTOR_``
    prefix by default) or from a ``.env`` file in the working directory.
    """

    model_config = SettingsConfigDict(
        env_prefix="TUTOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- General ----------
    env: Literal["development", "staging", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    language: Literal["zh", "en"] = "zh"
    data_dir: Path = Field(default=Path("./data"))

    # ---------- Server ----------
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # ---------- LLM ----------
    llm_provider: Literal["openai", "anthropic", "deepseek", "azure_openai", "ollama", "custom"] = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096
    llm_timeout: int = 60

    # Per-agent override (optional)
    agent_llm_model: str = ""
    agent_llm_api_key: str = ""
    agent_llm_base_url: str = ""

    # ---------- Embedding ----------
    embed_provider: str = "openai"
    embed_model: str = "text-embedding-3-small"
    embed_api_key: str = ""
    embed_base_url: str = "https://api.openai.com/v1"

    # ---------- RAG ----------
    rag_provider: Literal["llamaindex"] = "llamaindex"
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 50
    rag_top_k: int = 5

    # ---------- Knowledge Base ----------
    kb_default: str = "ai_introduction"
    kb_dir: Path = Field(default=Path("./backend/tutor/knowledge_base"))

    # ---------- Manim ----------
    manim_enabled: bool = True
    manim_quality: Literal["l", "m", "h"] = "l"
    manim_timeout: int = 600
    manim_output_dir: Path = Field(
        default=Path("./backend/tutor/services/manim_render/output")
    )
    manim_temp_dir: Path = Field(
        default=Path("./backend/tutor/services/manim_render/temp")
    )
    code_retry_max_attempts: int = 4

    # ---------- Web Search ----------
    web_search_enabled: bool = False
    web_search_provider: Literal["duckduckgo", "searxng", "bing"] = "duckduckgo"
    web_search_max_results: int = 5

    # ---------- Anti-Hallucination ----------
    anti_hallucination_enabled: bool = True
    fact_check_confidence_threshold: float = 0.7
    content_safety_enabled: bool = True

    # ---------- Profile ----------
    profile_min_conversation_rounds: int = 2
    profile_update_interval_seconds: int = 300

    # ---------- Streaming ----------
    stream_chunk_size: int = 20
    stream_queue_max_size: int = 1000

    # ---------- Multi-user ----------
    multi_user_enabled: bool = False
    auth_secret: str = ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide :class:`Settings` (cached)."""
    # Allow override via cwd .env in development.
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings. Used by tests and after editing ``.env``."""
    get_settings.cache_clear()


__all__ = ["Settings", "get_settings", "reset_settings_cache"]
