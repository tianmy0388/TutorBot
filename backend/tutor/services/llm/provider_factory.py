"""LLM provider factory.

Given a ``Settings`` or a configuration dict, returns the appropriate
:class:`LLMProvider` implementation. New providers can be registered
via :func:`register_provider`.

Design inspired by DeepTutor's :class:`deeptutor.services.llm.provider_factory`.
"""

from __future__ import annotations

from typing import Any

from tutor.services.config.settings import Settings
from tutor.services.llm.anthropic import AnthropicProvider
from tutor.services.llm.base import LLMProvider
from tutor.services.llm.openai_compat import OpenAICompatProvider

_PROVIDERS: dict[str, type[LLMProvider]] = {
    "openai": OpenAICompatProvider,
    "deepseek": OpenAICompatProvider,
    "ollama": OpenAICompatProvider,
    "azure_openai": OpenAICompatProvider,
    "custom": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
}


def register_provider(name: str, cls: type[LLMProvider]) -> None:
    """Register a custom provider class under ``name``."""
    _PROVIDERS[name] = cls


def list_providers() -> list[str]:
    """Return the names of all registered providers."""
    return sorted(_PROVIDERS.keys())


def get_runtime_provider(
    settings: Settings | None = None,
    *,
    config: dict[str, Any] | None = None,
) -> LLMProvider:
    """Build an :class:`LLMProvider` from settings or a config dict.

    Parameters
    ----------
    settings : Settings, optional
        Use these settings. Defaults to :func:`tutor.services.config.settings.get_settings`.
    config : dict, optional
        Override individual keys ``{provider, model, api_key, base_url, ...}``.
        Useful for per-request overrides.
    """
    if settings is None:
        from tutor.services.config.settings import get_settings

        settings = get_settings()

    cfg = dict(config or {})
    provider_name = cfg.get("provider") or settings.llm_provider
    model = cfg.get("model") or settings.llm_model
    api_key = cfg.get("api_key") or settings.llm_api_key
    base_url = cfg.get("base_url") or settings.llm_base_url

    # Per-provider defaults
    if provider_name == "deepseek" and not base_url:
        base_url = "https://api.deepseek.com/v1"
    if provider_name == "ollama" and not base_url:
        base_url = "http://localhost:11434/v1"

    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        raise ValueError(
            f"Unknown LLM provider: {provider_name!r}. "
            f"Known: {list_providers()}"
        )

    return cls(
        model=model,
        api_key=api_key,
        base_url=base_url,
        default_temperature=cfg.get("temperature", settings.llm_temperature),
        default_max_tokens=cfg.get("max_tokens", settings.llm_max_tokens),
        timeout=cfg.get("timeout", settings.llm_timeout),
    )


__all__ = ["get_runtime_provider", "list_providers", "register_provider"]
