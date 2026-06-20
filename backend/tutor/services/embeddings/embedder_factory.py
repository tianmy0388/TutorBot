"""Embedder provider factory.

Given a :class:`Settings` (or override dict), return the right
:class:`Embedder` implementation. New providers can be registered via
:func:`register_embedder`.
"""

from __future__ import annotations

from typing import Any

from tutor.services.config.settings import Settings
from tutor.services.embeddings.base import Embedder
from tutor.services.embeddings.openai_compat import OpenAICompatEmbedder

# All currently-supported embedder providers are OpenAI-compatible.
# Future: add cohere, jina, voyage, etc. here as their own classes.
_PROVIDERS: dict[str, type[Embedder]] = {
    "openai": OpenAICompatEmbedder,
    "openrouter": OpenAICompatEmbedder,
    "ollama": OpenAICompatEmbedder,
    "custom": OpenAICompatEmbedder,
    # Aliases — same class, different ``provider`` string in .env.
    "deepseek": OpenAICompatEmbedder,
    "azure_openai": OpenAICompatEmbedder,
}


def register_embedder(name: str, cls: type[Embedder]) -> None:
    """Register a custom embedder class under ``name``."""
    _PROVIDERS[name] = cls


def list_embedders() -> list[str]:
    """Return the names of all registered embedder providers."""
    return sorted(_PROVIDERS.keys())


def get_runtime_embedder(
    settings: Settings | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> Embedder:
    """Build an :class:`Embedder` from settings or a config dict.

    Parameters
    ----------
    settings : Settings, optional
        Use these settings. Defaults to :func:`tutor.services.config.settings.get_settings`.
    config : dict, optional
        Override individual keys ``{provider, model, api_key, base_url, ...}``.
    """
    if settings is None:
        from tutor.services.config.settings import get_settings

        settings = get_settings()

    cfg = dict(config or {})
    provider_name = (cfg.get("provider") or settings.embed_provider).lower()
    model = cfg.get("model") or settings.embed_model
    api_key = cfg.get("api_key") or settings.embed_api_key
    base_url = cfg.get("base_url") or settings.embed_base_url
    dimensions = int(cfg.get("dimensions") or settings.embed_dimensions or 0)

    # Per-provider defaults (only used when env didn't set them).
    if provider_name == "openrouter" and not base_url:
        base_url = "https://openrouter.ai/api/v1"
    if provider_name == "ollama" and not base_url:
        base_url = "http://localhost:11434/v1"

    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        raise ValueError(
            f"Unknown embedder provider: {provider_name!r}. "
            f"Known: {list_embedders()}"
        )

    return cls(
        model=model,
        api_key=api_key,
        base_url=base_url,
        default_dimensions=dimensions,
    )


__all__ = ["get_runtime_embedder", "list_embedders", "register_embedder"]
