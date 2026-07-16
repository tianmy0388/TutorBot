"""Tests for the runtime configuration service (Task 6)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tutor.services.config.runtime import (
    EMBED_PROVIDERS,
    LLM_PROVIDERS,
    RuntimeConfigService,
    WEB_SEARCH_PROVIDERS,
    mask_key,
)
from tutor.services.config.settings import (
    get_settings,
    reset_settings_cache,
)


def _make_env(tmp_path: Path) -> Path:
    env = tmp_path / ".env"
    env.write_text(
        "# initial test env\n"
        "TUTOR_LLM_PROVIDER=openai\n"
        "TUTOR_LLM_MODEL=gpt-4o-mini\n"
        "TUTOR_LLM_API_KEY=sk-existing-key-1234567890\n"
        "TUTOR_EMBED_PROVIDER=openai\n"
        "TUTOR_EMBED_MODEL=text-embedding-3-small\n"
        "TUTOR_WEB_SEARCH_PROVIDER=duckduckgo\n"
        "TUTOR_WEB_SEARCH_ENABLED=false\n",
        encoding="utf-8",
    )
    return env


def test_get_masks_existing_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    _make_env(tmp_path)
    # Settings looks for the .env relative to cwd; tell it to use ours.
    monkeypatch.setenv("TUTOR_LLM_API_KEY", "sk-existing-key-1234567890")
    monkeypatch.setenv("TUTOR_LLM_PROVIDER", "openai")
    monkeypatch.setenv("TUTOR_LLM_MODEL", "gpt-4o-mini")
    reset_settings_cache()
    svc = RuntimeConfigService(env_path=tmp_path / ".env")
    snapshot = svc.read()
    assert "api_key" in snapshot["llm"]
    assert snapshot["llm"]["api_key"]["configured"] is True
    # No raw key anywhere in the response.
    raw = str(snapshot)
    assert "sk-existing-key-1234567890" not in raw
    # Preview is masked
    assert (
        "…" in snapshot["llm"]["api_key"]["preview"]
        or "*" in snapshot["llm"]["api_key"]["preview"]
    )


def test_get_unconfigured_key_returns_false(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    reset_settings_cache()
    svc = RuntimeConfigService(env_path=tmp_path / "missing.env")
    snapshot = svc.read()
    assert snapshot["llm"]["api_key"]["configured"] is False
    assert snapshot["embedding"]["api_key"]["configured"] is False


def test_deepseek_llm_without_embedding_key_returns_clear_hint(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("TUTOR_EMBED_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "TUTOR_LLM_PROVIDER=deepseek\n"
        "TUTOR_LLM_MODEL=deepseek-chat\n"
        "TUTOR_LLM_API_KEY=fake-deepseek-key\n"
        "TUTOR_EMBED_PROVIDER=openai\n"
        "TUTOR_EMBED_MODEL=text-embedding-3-small\n",
        encoding="utf-8",
    )
    reset_settings_cache()
    svc = RuntimeConfigService(env_path=env)
    snapshot = svc.read()
    hint = snapshot["embedding"]["api_key"]["hint"]
    assert snapshot["llm"]["provider"] == "deepseek"
    assert snapshot["embedding"]["api_key"]["configured"] is False
    assert "DeepSeek" in hint
    assert "separate" in hint


def test_patch_with_null_api_key_preserves_existing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    _make_env(tmp_path)
    reset_settings_cache()
    svc = RuntimeConfigService(env_path=tmp_path / ".env")
    # Apply a patch with only provider change + api_key=None
    from tutor.services.config.runtime import LLMSectionPatch

    patch = LLMSectionPatch(provider="openai", model="gpt-4o")
    svc.apply_llm(patch)
    # The existing key must still be on disk
    contents = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "sk-existing-key-1234567890" in contents


def test_patch_with_clear_api_key_removes_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    _make_env(tmp_path)
    reset_settings_cache()
    svc = RuntimeConfigService(env_path=tmp_path / ".env")
    from tutor.services.config.runtime import LLMSectionPatch

    patch = LLMSectionPatch(clear_api_key=True)
    svc.apply_llm(patch)
    contents = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "sk-existing-key-1234567890" not in contents
    # And the masked read says not configured
    snapshot = svc.read()
    assert snapshot["llm"]["api_key"]["configured"] is False


def test_patch_with_explicit_api_key_replaces(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    _make_env(tmp_path)
    reset_settings_cache()
    svc = RuntimeConfigService(env_path=tmp_path / ".env")
    from tutor.services.config.runtime import LLMSectionPatch

    patch = LLMSectionPatch(api_key="sk-new-key-abcdef")
    svc.apply_llm(patch)
    contents = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "sk-new-key-abcdef" in contents
    assert "sk-existing-key-1234567890" not in contents


def test_atomic_write_uses_sibling_tempfile(tmp_path, monkeypatch) -> None:
    """Verify .env.replace() style atomic write happens via a sibling
    temporary file. We can't easily observe Path.replace, so we assert
    that the .env.tmp file does not exist after the write (it was
    replaced)."""
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    _make_env(tmp_path)
    reset_settings_cache()
    svc = RuntimeConfigService(env_path=tmp_path / ".env")
    from tutor.services.config.runtime import LLMSectionPatch

    patch = LLMSectionPatch(model="gpt-4o")
    svc.apply_llm(patch)
    # No leftover .env.tmp files
    leftovers = list(tmp_path.glob(".env.tmp*"))
    assert leftovers == []


def test_invalid_provider_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    _make_env(tmp_path)
    reset_settings_cache()
    from pydantic import ValidationError

    from tutor.services.config.runtime import LLMSectionPatch

    with pytest.raises(ValidationError):
        LLMSectionPatch(provider="not-a-provider")


def test_providers_constants_match_settings() -> None:
    # These constants are the source of truth for the HTTP API.
    assert "openai" in LLM_PROVIDERS
    assert "anthropic" in LLM_PROVIDERS
    assert "spark" in LLM_PROVIDERS
    assert "openai" in EMBED_PROVIDERS
    assert "duckduckgo" in WEB_SEARCH_PROVIDERS


def test_spark_provider_applies_official_openai_compatible_preset(
    tmp_path, monkeypatch,
) -> None:
    monkeypatch.setenv("TUTOR_DATA_DIR", str(tmp_path / "data"))
    _make_env(tmp_path)
    reset_settings_cache()
    from tutor.services.config.runtime import LLMSectionPatch

    svc = RuntimeConfigService(env_path=tmp_path / ".env")
    svc.apply_llm(LLMSectionPatch(provider="spark"))
    contents = (tmp_path / ".env").read_text(encoding="utf-8")

    assert "TUTOR_LLM_PROVIDER=spark" in contents
    assert "TUTOR_LLM_MODEL=4.0Ultra" in contents
    assert "TUTOR_LLM_BASE_URL=https://spark-api-open.xf-yun.com/v1" in contents


def test_spark_provider_factory_uses_compatible_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("TUTOR_LLM_BASE_URL", raising=False)
    from tutor.services.config.settings import Settings
    from tutor.services.llm.provider_factory import get_runtime_provider

    settings = Settings(
        llm_provider="spark",
        llm_model="4.0Ultra",
        llm_api_key="fake-spark-api-password",
        llm_base_url="",
    )
    provider = get_runtime_provider(settings)

    assert provider.model == "4.0Ultra"
    assert str(provider._client.base_url).rstrip("/") == (
        "https://spark-api-open.xf-yun.com/v1"
    )


def test_mask_key_short_string() -> None:
    masked = mask_key("abcd")
    assert masked.configured is True
    assert masked.preview == "****"


def test_mask_key_empty() -> None:
    masked = mask_key("")
    assert masked.configured is False
    assert masked.preview == ""


def test_mask_key_long() -> None:
    masked = mask_key("sk-1234567890abcdef")
    assert masked.configured is True
    assert masked.preview.startswith("sk-")
    assert "…" in masked.preview
    # The middle of the key is never exposed
    assert "1234567890abcdef" not in masked.preview
