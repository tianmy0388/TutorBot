"""Embedder provider factory.

Given a :class:`Settings` (or override dict), return the right
:class:`Embedder` implementation. New providers can be registered via
:func:`register_embedder`.

2026-06-21 plan: Zhipu (智谱) is registered as a first-class
provider. Their embedding API is OpenAI-compatible (base URL
``https://open.bigmodel.cn/api/paas/v4``) and ships
``embedding-2`` plus the new ``embedding-3`` family that supports
dimensions of 1024 / 768 / 512 — the spec calls out that the
provider must accept an explicit ``dimensions`` config and that
the index manifest must record it so the RAG index lifecycle can
flag mismatched embeddings.
"""

from __future__ import annotations

from typing import Any

from tutor.services.config.settings import Settings
from tutor.services.embeddings.base import Embedder
from tutor.services.embeddings.local_hash import LocalHashEmbedder
from tutor.services.embeddings.openai_compat import OpenAICompatEmbedder

# All currently-supported embedder providers are OpenAI-compatible.
# Future: add cohere, jina, voyage, etc. here as their own classes.
_PROVIDERS: dict[str, type[Embedder]] = {
    "local": LocalHashEmbedder,
    "openai": OpenAICompatEmbedder,
    "openrouter": OpenAICompatEmbedder,
    "ollama": OpenAICompatEmbedder,
    "custom": OpenAICompatEmbedder,
    # Aliases — same class, different ``provider`` string in .env.
    "deepseek": OpenAICompatEmbedder,
    "azure_openai": OpenAICompatEmbedder,
    # 2026-06-21 plan: Zhipu (智谱) — OpenAI-compatible endpoint
    # at open.bigmodel.cn. Defaults below are applied when env
    # values are missing.
    "zhipu": OpenAICompatEmbedder,
    "zhipuai": OpenAICompatEmbedder,
}

# Per-provider defaults applied when env didn't set them. Keyed by
# lowercase provider name. Each entry is a partial kwargs dict
# passed straight into the embedder constructor.
_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "local": {
        "model": "local-hash-v1",
        "base_url": "",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
    },
    "zhipu": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "embedding-3",
    },
    "zhipuai": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "embedding-3",
    },
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

    # Apply per-provider defaults BEFORE reading individual fields,
    # so explicit cfg values still win. This is the path that gives
    # Zhipu a sensible base URL + default model out of the box.
    defaults = _PROVIDER_DEFAULTS.get(provider_name, {})
    if "model" in defaults and not (cfg.get("model") or settings.embed_model):
        cfg.setdefault("model", defaults["model"])
    if "base_url" in defaults and not (
        cfg.get("base_url") or settings.embed_base_url
    ):
        cfg.setdefault("base_url", defaults["base_url"])

    model = cfg.get("model") or settings.embed_model
    api_key = cfg.get("api_key") or settings.embed_api_key
    base_url = cfg.get("base_url") or settings.embed_base_url
    dimensions = int(cfg.get("dimensions") or settings.embed_dimensions or 0)

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
