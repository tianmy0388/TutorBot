"""Centralised application settings, loaded from environment variables.

We use :mod:`pydantic_settings` so values are typed, validated, and
introspectable. The :func:`get_settings` helper caches a singleton
process-wide.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _repo_root() -> Path:
    """Return the repository root independently of the process cwd."""
    return Path(__file__).resolve().parents[4]


def resolve_path(value: Path) -> Path:
    """Resolve an absolute path or anchor a relative path at the repo root."""
    if value.is_absolute():
        return value.resolve()
    return (_repo_root() / value).resolve()


def _default_data_dir() -> Path:
    """Return the single canonical, repository-root data directory."""
    return _repo_root() / "data"


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

    data_dir: Path = Field(default_factory=_default_data_dir)

    @field_validator("data_dir", mode="after")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        """Always store the data_dir as an absolute, fully-resolved
        path. This eliminates the original bug where running the
        backend from ``backend/`` would create ``backend/data/`` while
        running it from the project root created ``data/`` — two
        separate SQLite databases for the same user.

        Relative paths are interpreted relative to the project root
        (the same anchor the default uses), not relative to the
        process cwd. The on-disk health check / startup banner prints
        the resolved path so the operator can see exactly which
        directory the process is using.
        """
        return resolve_path(value)

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
    # 2026-06-21 plan: ``zhipu`` / ``zhipuai`` are first-class
    # provider strings. The factory registers Zhipu as an
    # OpenAI-compatible provider and applies a sensible default
    # ``base_url`` + ``model`` (``embedding-3``) when env doesn't
    # override them.
    embed_provider: Literal[
        "openai", "openrouter", "ollama", "custom", "deepseek",
        "azure_openai", "zhipu", "zhipuai",
    ] = "openai"
    embed_model: str = "text-embedding-3-small"
    embed_api_key: str = ""
    embed_base_url: str = "https://api.openai.com/v1"
    embed_dimensions: int = 0  # 0 = use provider default; only some models support override

    # 2026-06-21 plan (D12): explicit keyword-only fallback policy.
    # The pre-fix behaviour silently fell back to text-only
    # matching when the embedder raised an exception, which
    # produced "ready" documents that were actually invisible to
    # vector retrieval. The spec calls for the default to be
    # ``False`` — vector failure means the document is
    # ``failed / EMBED_FAILED`` and never reaches the
    # retrieval index. Operators who really do want the
    # text-only fallback (e.g. local dev without an API key)
    # can opt in here.
    embedding_keyword_fallback: bool = False

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

    # ---------- Code execution (2026-06-21 plan) ----------
    # Path to the Python interpreter used by ``CodeSandboxAgent`` and
    # the code-execution tool. The default is the Python that
    # launched the backend (``sys.executable``) — but for development
    # this should point at the ``tutor`` conda env that has matplotlib
    # and manim installed. The Windows dev script in ``package.json``
    # launches the backend via ``conda run -n tutor`` so this default
    # is correct; setting it explicitly here is the safety net for
    # callers that launch the backend some other way (e.g. systemd,
    # Docker).
    execution_python: str = ""
    # Hard timeout for any single code run.
    code_run_timeout_seconds: int = 15
    # Sub-directory under ``data_dir`` where per-run scratch files
    # and image artifacts are written.
    code_run_subdir: str = "code_runs"

    @field_validator("execution_python", mode="after")
    @classmethod
    def _fill_execution_python(cls, value: str) -> str:
        # Empty string ⇒ defer to sys.executable at runtime so tests
        # that monkeypatch sys.executable pick the new value up.
        return value or ""

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

    # ---------- Job ----------
    # Maximum wall-clock time for any single job (seconds). After this
    # timeout the job is transitioned to FAILED with code
    # ``JOB_TIMEOUT``. 0 = no timeout. The timeout is enforced by the
    # async runner: when ``run_task`` exceeds this, a cancellation is
    # issued and the job exits cleanly.
    job_timeout_seconds: int = 600

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


__all__ = ["Settings", "get_settings", "reset_settings_cache", "resolve_path"]
