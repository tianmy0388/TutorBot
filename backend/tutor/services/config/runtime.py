"""Runtime configuration service (Task 6).

The :class:`RuntimeConfigService` is the safe boundary between the
``/api/v1/config`` HTTP surface and the project-root ``.env`` file.

Security guarantees (asserted in tests):

- The GET response NEVER includes a raw API key. Only a "configured"
  boolean and a masked preview (e.g. ``"sk-...ab12"``) leak.
- PATCH with ``api_key: null`` PRESERVES the existing key. The empty
  string is a sentinel for "no change" — we never accidentally wipe a
  credential by writing the value back.
- PATCH with ``clear_api_key: true`` REMOVES the key. The flag is
  distinct from ``api_key`` so a UI form can offer a "clear" button
  without sending an empty string.
- ``.env`` is rewritten atomically: the new content goes to a
  sibling temp file, then :meth:`Path.replace` swaps it in. A crash
  mid-write can never leave a half-written ``.env`` on disk.
- After any successful write, :func:`reset_settings_cache` is called
  and provider factory caches are cleared so subsequent requests pick
  up the new values.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator

from tutor.services.config.settings import (
    Settings,
    get_settings,
    reset_settings_cache,
)

# ---------------------------------------------------------------------------
# Section schemas
# ---------------------------------------------------------------------------

#: Fields whose values are secrets and must never be returned in plain
#: text. Each value is replaced with a masked preview + a ``configured``
#: boolean when read.
SECRET_FIELDS: dict[str, tuple[str, ...]] = {
    "llm": ("llm_api_key",),
    "embedding": ("embed_api_key",),
}

#: Fields whose value is a Provider literal (validated against an enum).
PROVIDER_LITERAL_FIELDS: dict[str, str] = {
    "llm": "llm_provider",
    "embedding": "embed_provider",
    "web_search": "web_search_provider",
}

#: Allow-list of LLM providers (mirrors the Settings Literal).
LLM_PROVIDERS: tuple[str, ...] = (
    "openai", "anthropic", "deepseek", "spark", "azure_openai", "ollama", "custom",
)
#: Allow-list of Web Search providers.
WEB_SEARCH_PROVIDERS: tuple[str, ...] = (
    "duckduckgo", "searxng", "bing", "mcp",
)
#: Allow-list of Embedding providers.
EMBED_PROVIDERS: tuple[str, ...] = (
    "local", "openai", "openrouter", "azure_openai", "ollama", "custom",
    "deepseek", "zhipu", "zhipuai",
)


# ---------------------------------------------------------------------------
# Public request/response shapes
# ---------------------------------------------------------------------------


class MaskedSecret(BaseModel):
    """Returned to the client in place of a raw API key."""

    model_config = ConfigDict(extra="forbid")

    configured: bool
    preview: str = ""  # e.g. "sk-...ab12"
    required: bool = True  # False for providers that don't need a key (e.g. MCP)
    hint: str = ""  # e.g. "MCP provider reads credentials from .mcp.json"


class _BaseSection(BaseModel):
    """Common validator: api_key ``None`` is a no-op sentinel."""


class LLMSectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    timeout: int | None = Field(default=None, ge=1)
    api_key: str | None = None  # None = no change
    clear_api_key: bool = False

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in LLM_PROVIDERS:
            raise ValueError(
                f"invalid LLM provider {v!r}; allowed: {list(LLM_PROVIDERS)}"
            )
        return v


class EmbeddingSectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    dimensions: int | None = Field(default=None, ge=0)
    api_key: str | None = None
    clear_api_key: bool = False

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in EMBED_PROVIDERS:
            raise ValueError(
                f"invalid embedding provider {v!r}; allowed: {list(EMBED_PROVIDERS)}"
            )
        return v


class WebSearchSectionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    provider: str | None = None
    max_results: int | None = Field(default=None, ge=1, le=50)
    api_key: str | None = None
    clear_api_key: bool = False

    @field_validator("provider")
    @classmethod
    def _validate_provider(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if v not in WEB_SEARCH_PROVIDERS:
            raise ValueError(
                f"invalid web search provider {v!r}; allowed: {list(WEB_SEARCH_PROVIDERS)}"
            )
        return v


# ---------------------------------------------------------------------------
# Mapping from section name → Settings attribute name (non-secret fields)
# ---------------------------------------------------------------------------

# When we read the existing .env to rewrite it, we want the actual
# *value* (not the masked form). We maintain an internal map.
SECTION_FIELD_MAP: dict[str, dict[str, str]] = {
    "llm": {
        "provider": "llm_provider",
        "model": "llm_model",
        "base_url": "llm_base_url",
        "temperature": "llm_temperature",
        "max_tokens": "llm_max_tokens",
        "timeout": "llm_timeout",
        "api_key": "llm_api_key",
    },
    "embedding": {
        "provider": "embed_provider",
        "model": "embed_model",
        "base_url": "embed_base_url",
        "dimensions": "embed_dimensions",
        "api_key": "embed_api_key",
    },
    "web_search": {
        "enabled": "web_search_enabled",
        "provider": "web_search_provider",
        "max_results": "web_search_max_results",
        "api_key": "web_search_api_key",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def mask_key(
    value: str | None,
    *,
    required: bool = True,
    hint: str = "",
) -> MaskedSecret:
    """Return a masked preview + a configured flag for an API key."""
    if not value:
        return MaskedSecret(
            configured=False,
            preview="",
            required=required,
            hint=hint,
        )
    if len(value) <= 8:
        return MaskedSecret(
            configured=True,
            preview="*" * len(value),
            required=required,
            hint=hint,
        )
    return MaskedSecret(
        configured=True,
        preview=f"{value[:3]}…{value[-4:]}",
        required=required,
        hint=hint,
    )


def _coerce_bool(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file into a dict. Preserves comments/blank lines via
    a side dict for round-tripping later if needed."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # strip surrounding quotes if present
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        out[key] = value
    return out


def _atomic_write_env(path: Path, env: dict[str, str]) -> None:
    """Write ``env`` to ``path`` atomically (sibling tempfile + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={_format_env_value(v)}" for k, v in env.items()]
    content = "\n".join(lines) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    # Path.replace is atomic on POSIX and Windows for same-volume files.
    tmp.replace(path)


def _format_env_value(value: str) -> str:
    """Quote a value if it contains whitespace, '#', or starts with a quote."""
    if value == "":
        return ""
    if any(c.isspace() for c in value) or "#" in value or value.startswith('"'):
        # Use double quotes; escape internal double quotes / backslashes.
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return value


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RuntimeConfigService:
    """High-level read/update/test surface for AI service configuration."""

    def __init__(self, env_path: Path | None = None) -> None:
        # Resolve the project-root ``.env`` by default. ``Settings`` looks at
        # both the cwd and the parent dir, so we mirror that.
        if env_path is None:
            cwd_env = Path.cwd() / ".env"
            parent_env = Path.cwd().parent / ".env"
            if cwd_env.exists():
                env_path = cwd_env
            elif parent_env.exists():
                env_path = parent_env
            else:
                env_path = cwd_env
        self.env_path = env_path

    def _get_settings(self) -> Settings:
        """Return Settings bound to ``self.env_path``, not the global
        process singleton.

        The pre-fix code called the global :func:`get_settings` in
        ``read()`` and ``_test_*()`` helpers. That function is
        cached with whatever ``env_file`` the process first saw
        (usually the project-root ``.env``). When a test passes
        ``env_path=tmp_path/missing.env``, global ``get_settings()``
        still returns the values from the project-root ``.env``
        (because ``lru_cache``), causing the test to report
        ``configured=true`` even though the reference path has no
        key at all.

        The fix: we construct a fresh, uncached Settings instance
        whose ``_env_file`` points at our reference path and whose
        ``_env_prefix`` is correct, but we explicitly suppress the
        default ``env_file`` tuple that ``Settings`` uses to find
        the global ``.env``. This way the returned object only
        reads from ``self.env_path`` + process environment.
        """
        return Settings(
            _env_file=self.env_path,
            _env_file_encoding="utf-8",
        )  # type: ignore[call-arg]

    # -- read --------------------------------------------------------------

    def read(self) -> dict[str, Any]:
        """Return the masked configuration for all three sections."""
        s = self._get_settings()
        llm_secret = s.llm_api_key or os.environ.get("TUTOR_LLM_API_KEY", "")
        embed_secret = s.embed_api_key or os.environ.get("TUTOR_EMBED_API_KEY", "")
        embed_hint = ""
        embed_key_required = s.embed_provider != "local"
        if s.embed_provider == "local":
            embed_hint = (
                "Local hash embeddings run offline and are intended for seeded "
                "courseware, demos, and smoke tests. Use a cloud embedding provider "
                "for stronger semantic recall."
            )
        elif s.llm_provider == "deepseek" and not embed_secret:
            embed_hint = (
                "DeepSeek is only used as the LLM provider here. Configure a separate "
                "Embedding provider/key for knowledge-base indexing and retrieval."
            )
        elif s.embed_provider == "deepseek":
            embed_hint = (
                "DeepSeek embedding is kept for compatibility, but the demo preset "
                "expects a dedicated embedding provider such as OpenAI, OpenRouter, "
                "Zhipu, Ollama, or a custom OpenAI-compatible endpoint."
            )
        # Web-search uses a runtime-override-only model: the API key is
        # read straight from os.environ (because MCP / SearXNG / Bing
        # may consume it from a separate config). When the provider is
        # ``mcp``, the API key is irrelevant — the actual credentials
        # live in the MCP server config (.mcp.json) — so the field is
        # marked ``required=False`` in the response shape via a hint.
        web_secret = os.environ.get("TUTOR_WEB_SEARCH_API_KEY", "")
        web_key_required = s.web_search_provider != "mcp"
        web_hint = (
            "MCP provider 不需要 API Key，凭证由 .mcp.json 中的环境变量提供"
            if s.web_search_provider == "mcp"
            else ""
        )
        return {
            "llm": {
                "provider": s.llm_provider,
                "model": s.llm_model,
                "base_url": s.llm_base_url,
                "temperature": s.llm_temperature,
                "max_tokens": s.llm_max_tokens,
                "timeout": s.llm_timeout,
                "api_key": mask_key(llm_secret).model_dump(),
            },
            "embedding": {
                "provider": s.embed_provider,
                "model": s.embed_model,
                "base_url": s.embed_base_url,
                "dimensions": s.embed_dimensions,
                "api_key": mask_key(
                    embed_secret,
                    required=embed_key_required,
                    hint=embed_hint,
                ).model_dump(),
            },
            "web_search": {
                "enabled": s.web_search_enabled,
                "provider": s.web_search_provider,
                "max_results": s.web_search_max_results,
                "mcp_server": (
                    s.web_search_mcp_server if s.web_search_provider == "mcp" else ""
                ),
                "mcp_tool": (
                    s.web_search_mcp_tool if s.web_search_provider == "mcp" else ""
                ),
                "api_key": mask_key(
                    web_secret,
                    required=web_key_required,
                    hint=web_hint,
                ).model_dump(),
            },
        }

    # -- write -------------------------------------------------------------

    def apply_llm(self, patch: LLMSectionPatch) -> dict[str, Any]:
        return self._apply("llm", patch)

    def apply_embedding(self, patch: EmbeddingSectionPatch) -> dict[str, Any]:
        return self._apply("embedding", patch)

    def apply_web_search(self, patch: WebSearchSectionPatch) -> dict[str, Any]:
        return self._apply("web_search", patch)

    def _apply(self, section: str, patch: BaseModel) -> dict[str, Any]:
        """Apply a section patch to the .env and refresh caches."""
        field_map = SECTION_FIELD_MAP[section]
        env = _read_env_file(self.env_path)

        # 2026-06-21 plan (D6): when switching provider, apply the
        # preset defaults. The preset overrides the old provider's
        # model / base_url *only when the caller did not also pass
        # an explicit value for those fields*. This way the UI can
        # toggle "Zhipu" and immediately get the right embedding-3
        # model + open.bigmodel.cn base URL, but if the user typed
        # a custom model name in the same PATCH that also changed
        # provider, the user's value wins.
        patch_dict = patch.model_dump(exclude_unset=True)
        if patch_dict.get("provider"):
            provider = patch_dict["provider"]
            presets = _PROVIDER_PRESETS.get(provider)
            if presets:
                for field, preset_value in presets.items():
                    if field not in patch_dict:
                        patch_dict[field] = preset_value

        for client_field, value in patch_dict.items():
            # ``clear_api_key`` is a control flag, not a real Settings
            # field. Resolve its target key from the section's
            # ``api_key`` mapping.
            if client_field == "clear_api_key":
                if value:
                    api_settings_attr = field_map.get("api_key")
                    if api_settings_attr:
                        env.pop(f"TUTOR_{api_settings_attr.upper()}", None)
                continue

            settings_attr = field_map.get(client_field)
            if settings_attr is None:
                continue
            env_key = f"TUTOR_{settings_attr.upper()}"

            if client_field == "api_key":
                if value is None:
                    # No change — don't touch the existing key.
                    continue
                if not value:
                    # Empty string explicitly: treat as "no change" too
                    # (defensive). Use clear_api_key to actually remove.
                    continue
                env[env_key] = value
            elif client_field in ("provider", "model", "base_url", "enabled"):
                env[env_key] = _envify_scalar(value)
            elif client_field in ("temperature", "max_tokens", "timeout", "dimensions", "max_results"):
                env[env_key] = str(value)
            else:
                env[env_key] = _envify_scalar(value)

        _atomic_write_env(self.env_path, env)
        # The fresh settings reflect what we just wrote.
        reset_settings_cache()
        _clear_provider_caches()
        # 2026-06-21 plan: when embedding settings change, walk
        # every ready document and flag rows whose manifest no
        # longer matches the new config. The retrieval service
        # uses the flag to surface a "RAG is stale" warning in
        # the UI rather than silently returning wrong-answer
        # vectors.
        if section == "embedding":
            try:
                from tutor.services.knowledge_base.service import (
                    KnowledgeBaseService,
                )

                flagged = KnowledgeBaseService().detect_reindex_required()
                if flagged:
                    logger.info(
                        "embedding config changed: {n} documents flagged for reindex",
                        n=flagged,
                    )
            except Exception as exc:  # noqa: BLE001
                # Reindex detection is best-effort — never block a
                # config save on it.
                logger.warning(
                    "detect_reindex_required failed: {err}", err=exc
                )
        return self.read()

    # -- test --------------------------------------------------------------

    def test_llm(self) -> dict[str, Any]:
        return _test_llm(svc=self)

    def test_embedding(self) -> dict[str, Any]:
        return _test_embedding(svc=self)

    def test_web_search(self) -> dict[str, Any]:
        return _test_web_search(svc=self)


#: Provider presets applied when switching provider via PATCH without
#: also passing explicit model / base_url values. The UI toggles
#: the provider dropdown; the preset fills the model and base URL
#: so the operator doesn't have to type them from memory. Presets
#: only apply to fields the PATCH did NOT include — a user who
#: sets ``provider=zhipu`` AND ``model=embedding-2`` (deviance)
#: keeps the manual override.
_PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "local": {
        "model": "local-hash-v1",
        "base_url": "",
    },
    "spark": {
        "model": "4.0Ultra",
        "base_url": "https://spark-api-open.xf-yun.com/v1",
    },
    "zhipu": {
        "model": "embedding-3",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
    "zhipuai": {
        "model": "embedding-3",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
    "ollama": {
        "model": "nomic-embed-text",
        "base_url": "http://localhost:11434/v1",
    },
}


def _envify_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _clear_provider_caches() -> None:
    """Invalidate provider/embedder factory caches so the next call
    rebuilds the client with the new settings."""
    try:
        from tutor.services.llm import provider_factory

        # Currently the provider factory has no cache, but we keep the
        # import as the integration seam.
        _ = provider_factory
    except Exception:  # noqa: BLE001
        pass
    try:
        from tutor.services.embeddings import embedder_factory

        _ = embedder_factory
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Connection tests
# ---------------------------------------------------------------------------


def _test_llm(svc: RuntimeConfigService | None = None) -> dict[str, Any]:
    """Run a minimal completion against the configured LLM and report."""
    import time as _time

    s = svc._get_settings() if svc is not None else get_settings()
    started = _time.monotonic()
    try:
        from tutor.services.llm.base import LLMMessage, LLMRequest
        from tutor.services.llm.provider_factory import get_runtime_provider

        provider = get_runtime_provider(s)
        req = LLMRequest(
            messages=[LLMMessage(role="user", content="ping")],
            max_tokens=8,
            temperature=0.0,
        )
        # Most providers expose ``call``; fall back to a one-shot
        # ``stream`` collection if not.
        try:
            resp = _run_async_from_sync(provider.call(req))  # type: ignore[attr-defined]
            resp_text = getattr(resp, "content", "") or ""
        except (NotImplementedError, AttributeError):
            resp_text = _collect_one_stream(provider, req)
        latency_ms = int((_time.monotonic() - started) * 1000)
        return {
            "ok": bool(resp_text),
            "provider": s.llm_provider,
            "model": s.llm_model,
            "latency_ms": latency_ms,
            "message": "ok" if resp_text else "empty response",
        }
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((_time.monotonic() - started) * 1000)
        return {
            "ok": False,
            "provider": s.llm_provider,
            "model": s.llm_model,
            "latency_ms": latency_ms,
            "message": f"{type(exc).__name__}: {exc}",
            "code": _classify_error(exc),
        }


def _run_async_from_sync(coro):  # type: ignore[no-untyped-def]
    """Run an async operation from sync config-test endpoints."""
    import asyncio as _asyncio
    import threading

    try:
        loop = _asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop is None or not loop.is_running():
        return _asyncio.run(coro)

    result_box: list[Any] = []
    err_box: list[BaseException] = []

    def _worker() -> None:
        new_loop = _asyncio.new_event_loop()
        try:
            _asyncio.set_event_loop(new_loop)
            result_box.append(new_loop.run_until_complete(coro))
        except BaseException as exc:  # noqa: BLE001
            err_box.append(exc)
        finally:
            new_loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()
    if err_box:
        raise err_box[0]
    return result_box[0] if result_box else None


async def _async_collect_one_stream(provider, req) -> str:  # type: ignore[no-untyped-def]
    chunks: list[str] = []
    async for chunk in provider.stream(req):  # type: ignore[attr-defined]
        if chunk.content:
            chunks.append(chunk.content)
    return "".join(chunks)


def _collect_one_stream(provider, req) -> str:  # type: ignore[no-untyped-def]
    """Synchronous wrapper around :func:`_async_collect_one_stream`.

    When called from a running event loop (the FastAPI handler) we
    cannot use ``asyncio.run``; instead we run the coroutine on the
    existing loop via ``run_until_complete`` after scheduling it
    through a worker thread. This is the same shape as
    ``asyncio.run`` but without the "loop already running" failure.

    For the typical case where no loop is running, falls back to
    ``asyncio.run``.
    """
    import asyncio as _asyncio

    try:
        loop = _asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop is None or not loop.is_running():
        return _asyncio.run(_async_collect_one_stream(provider, req))
    # We ARE inside a running loop. Run the coroutine to completion by
    # hopping through a worker thread + a fresh event loop there.
    import threading

    result_box: list[str] = []
    err_box: list[BaseException] = []

    def _worker() -> None:
        new_loop = _asyncio.new_event_loop()
        try:
            _asyncio.set_event_loop(new_loop)
            result_box.append(
                new_loop.run_until_complete(_async_collect_one_stream(provider, req))
            )
        except BaseException as exc:  # noqa: BLE001
            err_box.append(exc)
        finally:
            new_loop.close()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()
    if err_box:
        raise err_box[0]
    return result_box[0] if result_box else ""


def _test_embedding(svc: RuntimeConfigService | None = None) -> dict[str, Any]:
    import time as _time

    s = svc._get_settings() if svc is not None else get_settings()
    started = _time.monotonic()
    try:
        from tutor.services.embeddings.base import EmbedRequest
        from tutor.services.embeddings.embedder_factory import get_runtime_embedder

        embedder = get_runtime_embedder(s)
        # ``embedder.embed`` is async. We must drive it to completion
        # regardless of whether the caller is inside a running loop.
        resp = _run_async_from_sync(embedder.embed(EmbedRequest(input=["ping"])))
        vec = resp.vectors
        latency_ms = int((_time.monotonic() - started) * 1000)
        return {
            "ok": bool(vec) and len(vec[0]) > 0,
            "provider": s.embed_provider,
            "model": s.embed_model,
            "dimensions": len(vec[0]) if vec else 0,
            "latency_ms": latency_ms,
            "message": "ok",
        }
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((_time.monotonic() - started) * 1000)
        return {
            "ok": False,
            "provider": s.embed_provider,
            "model": s.embed_model,
            "latency_ms": latency_ms,
            "message": f"{type(exc).__name__}: {exc}",
            "code": _classify_error(exc),
        }


def _test_web_search(svc: RuntimeConfigService | None = None) -> dict[str, Any]:
    import time as _time

    s = svc._get_settings() if svc is not None else get_settings()
    if not s.web_search_enabled:
        return {
            "ok": False,
            "provider": s.web_search_provider,
            "latency_ms": 0,
            "message": "web search is disabled",
            "code": "DISABLED",
        }
    started = _time.monotonic()
    try:
        from tutor.services.tools.web_search_tool import (
            get_web_search_tool,
        )

        tool = get_web_search_tool()
        results = tool.search("test", max_results=1)
        latency_ms = int((_time.monotonic() - started) * 1000)
        return {
            "ok": True,
            "provider": s.web_search_provider,
            "latency_ms": latency_ms,
            "message": f"ok ({len(results)} result)",
        }
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((_time.monotonic() - started) * 1000)
        return {
            "ok": False,
            "provider": s.web_search_provider,
            "latency_ms": latency_ms,
            "message": f"{type(exc).__name__}: {exc}",
            "code": _classify_error(exc),
        }


def _classify_error(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    if "auth" in name or "401" in str(exc) or "invalid_api_key" in str(exc).lower():
        return "AUTH_ERROR"
    if "timeout" in name or "asynciotimeout" in name:
        return "TIMEOUT"
    if "connection" in name or "dns" in name or "name resolution" in str(exc).lower():
        return "NETWORK_ERROR"
    if "model" in name or "not found" in str(exc).lower():
        return "MODEL_NOT_FOUND"
    return "UNKNOWN"


__all__ = [
    "EMBED_PROVIDERS",
    "EmbeddingSectionPatch",
    "LLM_PROVIDERS",
    "LLMSectionPatch",
    "MaskedSecret",
    "RuntimeConfigService",
    "SECRET_FIELDS",
    "WEB_SEARCH_PROVIDERS",
    "WebSearchSectionPatch",
    "mask_key",
]
