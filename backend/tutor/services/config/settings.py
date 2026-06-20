"""Centralised application settings, loaded from environment variables.

We use :mod:`pydantic_settings` so values are typed, validated, and
introspectable. The :func:`get_settings` helper caches a singleton
process-wide.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide configuration.

    All values are populated from environment variables (with a ``TUTOR_``
    prefix by default) or from a ``.env`` file in the working directory.
    """

    model_config = SettingsConfigDict(
        env_prefix="TUTOR_",
        # pydantic-settings resolves ``env_file`` relative to the process
        # cwd, so running from ``backend/`` would miss the project-root
        # .env. Pass a tuple to try both locations.
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    @classmethod
    def parse_env_var(cls, field_name: str, raw_value: Any) -> Any:
        """Override pydantic-settings' env-var parser to accept comma-separated
        lists in addition to JSON-style lists.

        The ``.env`` file convention in this project uses
        ``TUTOR_CORS_ORIGINS=http://a,http://b``; pydantic-settings
        otherwise expects a JSON array. We split on commas and strip
        whitespace so the human-friendly form keeps working.
        """
        # Defer to the default for non-list fields.
        hint = cls.model_fields[field_name].annotation
        origin = getattr(hint, "__origin__", None)
        if origin is list and isinstance(raw_value, str) and not raw_value.lstrip().startswith(
            ("[", "{")
        ):
            return [item.strip() for item in raw_value.split(",") if item.strip()]
        return super().parse_env_var(field_name, raw_value)

    # ---------- General ----------
    env: Literal["development", "staging", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    language: Literal["zh", "en"] = "zh"
    data_dir: Path = Field(default=Path("./data"))

    # ---------- Server ----------
    host: str = "0.0.0.0"
    port: int = 8000
    # ``NoDecode`` (via Field metadata) tells pydantic-settings NOT to call
    # ``json.loads()`` on the raw env-string; the field_validator below
    # then handles both the human-friendly comma-separated form (``a,b``)
    # and the JSON form (``["a","b"]``).
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3010", "http://127.0.0.1:3010"],
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: Any) -> Any:
        """Allow comma-separated strings in ``.env`` (the common form),
        not just JSON arrays which pydantic-settings expects by default."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith(("[", "{")):
                return json.loads(stripped)
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

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
    embed_dimensions: int = 0  # 0 = use provider default; only some models support override

    # ---------- RAG ----------
    rag_provider: Literal["llamaindex"] = "llamaindex"
    rag_chunk_size: int = 512
    rag_chunk_overlap: int = 50
    rag_top_k: int = 5

    # ---------- Knowledge Base ----------
    kb_default: str = "ai_introduction"

    def _default_kb_dir() -> Path:
        # Resolve relative to this file so the default works regardless of
        # cwd (project root vs backend/). ``settings.py`` lives at
        # ``backend/tutor/services/config/settings.py`` — three levels up is
        # ``backend/tutor``, where ``knowledge_base/`` lives.
        return Path(__file__).resolve().parent.parent.parent / "knowledge_base"

    kb_dir: Path = Field(default_factory=_default_kb_dir)

    # ---------- Manim ----------
    manim_enabled: bool = True
    manim_quality: Literal["l", "m", "h"] = "l"
    manim_timeout: int = 600

    def _default_manim_output() -> Path:
        return Path(__file__).resolve().parent.parent / "manim_render" / "output"

    def _default_manim_temp() -> Path:
        return Path(__file__).resolve().parent.parent / "manim_render" / "temp"

    manim_output_dir: Path = Field(default_factory=_default_manim_output)
    manim_temp_dir: Path = Field(default_factory=_default_manim_temp)
    code_retry_max_attempts: int = 4

    # ---------- Web Search ----------
    web_search_enabled: bool = False
    web_search_provider: Literal["duckduckgo", "searxng", "bing", "mcp"] = "duckduckgo"
    web_search_max_results: int = 5

    # ---------- MCP ----------
    # Path to the MCP config file (JSON). When None, defaults to "./.mcp.json".
    mcp_config_path: Path | None = None
    # Web-search-via-MCP bindings: which server + which tool to call when
    # ``TUTOR_WEB_SEARCH_PROVIDER=mcp``.
    web_search_mcp_server: str = "MiniMax"
    web_search_mcp_tool: str = "web_search"

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
