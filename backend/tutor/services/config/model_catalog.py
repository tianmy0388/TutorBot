"""Model catalog — multi-profile LLM/embedding configuration.

A *catalog* is a JSON document mapping a logical service name
(``llm``, ``embedding``, ``agent_llm``...) to a *profile* and then
to a concrete *model spec*. This lets users swap models without
touching code.

Design inspired by DeepTutor's :class:`ModelCatalogService`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass
class ModelSpec:
    """Concrete model configuration for a single provider call."""

    provider: Literal[
        "openai", "anthropic", "deepseek", "spark", "azure_openai", "ollama", "custom"
    ]
    model: str
    api_key: str = ""
    base_url: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ModelProfile:
    """A named bundle of model specs for related services."""

    name: str
    description: str = ""
    models: dict[str, ModelSpec] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "models": {k: v.to_dict() for k, v in self.models.items()},
        }


@dataclass
class ModelCatalog:
    """Top-level catalog with named profiles and active selection."""

    profiles: dict[str, ModelProfile] = field(default_factory=dict)
    active: dict[str, str] = field(default_factory=dict)  # service_name -> profile_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "profiles": {k: v.to_dict() for k, v in self.profiles.items()},
            "active": dict(self.active),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelCatalog":
        profiles: dict[str, ModelProfile] = {}
        for name, prof in (data.get("profiles") or {}).items():
            profiles[name] = ModelProfile(
                name=name,
                description=prof.get("description", ""),
                models={
                    k: ModelSpec(**v) for k, v in (prof.get("models") or {}).items()
                },
            )
        return cls(profiles=profiles, active=dict(data.get("active") or {}))

    def get_active_profile(self, service: str) -> ModelProfile | None:
        profile_name = self.active.get(service)
        if not profile_name:
            return None
        return self.profiles.get(profile_name)

    def get_active_model(self, service: str) -> ModelSpec | None:
        profile = self.get_active_profile(service)
        if not profile:
            return None
        return profile.models.get(service)


class ModelCatalogService:
    """File-backed catalog. Persists to ``data/model_catalog.json``."""

    def __init__(self, path: str | Path = "data/model_catalog.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> ModelCatalog:
        if not self.path.exists():
            return ModelCatalog()
        with self.path.open("r", encoding="utf-8") as fh:
            return ModelCatalog.from_dict(json.load(fh))

    def save(self, catalog: ModelCatalog) -> None:
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(catalog.to_dict(), fh, ensure_ascii=False, indent=2)

    def apply_env(self, catalog: ModelCatalog) -> dict[str, str]:
        """Render the catalog as ``KEY=VALUE`` lines for ``.env``.

        Returns a dict of key -> value pairs the caller can persist.
        """
        env_vars: dict[str, str] = {}
        for service, profile_name in catalog.active.items():
            profile = catalog.profiles.get(profile_name)
            if not profile:
                continue
            spec = profile.models.get(service)
            if not spec:
                continue
            prefix = service.upper()
            env_vars[f"TUTOR_{prefix}_PROVIDER"] = spec.provider
            env_vars[f"TUTOR_{prefix}_MODEL"] = spec.model
            if spec.api_key:
                env_vars[f"TUTOR_{prefix}_API_KEY"] = spec.api_key
            if spec.base_url:
                env_vars[f"TUTOR_{prefix}_BASE_URL"] = spec.base_url
            env_vars[f"TUTOR_{prefix}_TEMPERATURE"] = str(spec.temperature)
            env_vars[f"TUTOR_{prefix}_MAX_TOKENS"] = str(spec.max_tokens)
        return env_vars


__all__ = ["ModelCatalog", "ModelCatalogService", "ModelProfile", "ModelSpec"]
