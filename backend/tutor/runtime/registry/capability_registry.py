"""CapabilityRegistry — registry of :class:`BaseCapability` instances.

Design inspired by DeepTutor's :class:`CapabilityRegistry`.
"""

from __future__ import annotations

import importlib
import threading
from functools import lru_cache
from typing import Any

from loguru import logger

from tutor.core.capability_protocol import BaseCapability


# Map capability name → "module:class" path. New capabilities are added here.
BUILTIN_CAPABILITY_CLASSES: dict[str, str] = {
    "profile": "tutor.capabilities.profile:LearnerProfileCapability",
    "resource_generation": "tutor.capabilities.resource_generation:ResourceGenerationCapability",
    "path_planning": "tutor.capabilities.path_planning:PathPlanningCapability",
    "tutoring": "tutor.capabilities.tutoring:TutoringCapability",
    "assessment": "tutor.capabilities.assessment:AssessmentCapability",
}


class CapabilityRegistry:
    """Thread-safe registry of capability instances."""

    def __init__(self) -> None:
        self._registry: dict[str, BaseCapability] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, capability: BaseCapability) -> None:
        name = capability.manifest.name
        with self._lock:
            if name in self._registry:
                logger.warning(f"Overwriting capability {name!r}")
            self._registry[name] = capability
            logger.debug(f"Registered capability: {name}")

    def unregister(self, name: str) -> None:
        with self._lock:
            self._registry.pop(name, None)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def get(self, name: str) -> BaseCapability | None:
        return self._registry.get(name)

    def get_required(self, name: str) -> BaseCapability:
        cap = self.get(name)
        if cap is None:
            raise KeyError(f"Capability {name!r} not found in registry")
        return cap

    def list_capabilities(self) -> list[str]:
        return sorted(self._registry.keys())

    def get_manifests(self) -> list[dict[str, Any]]:
        return [cap.manifest.to_dict() for cap in self._registry.values()]

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    def load_builtins(self) -> None:
        """Instantiate and register every builtin capability.

        Missing classes are logged and skipped — this allows the system
        to start even before every capability is implemented.
        """
        for name, path in BUILTIN_CAPABILITY_CLASSES.items():
            if name in self._registry:
                continue
            try:
                module_name, _, class_name = path.partition(":")
                module = importlib.import_module(module_name)
                cls = getattr(module, class_name)
                self.register(cls())
            except ModuleNotFoundError as exc:
                logger.warning(f"Capability {name!r}: module not found ({exc})")
            except (AttributeError, TypeError) as exc:
                logger.warning(f"Capability {name!r}: failed to load ({exc})")
            except Exception as exc:
                logger.error(f"Capability {name!r}: unexpected error ({exc!r})")

    def reset(self) -> None:
        """Clear the registry. Intended for tests."""
        with self._lock:
            self._registry.clear()


@lru_cache(maxsize=1)
def get_capability_registry() -> CapabilityRegistry:
    """Return the singleton :class:`CapabilityRegistry`."""
    reg = CapabilityRegistry()
    reg.load_builtins()
    return reg


__all__ = [
    "BUILTIN_CAPABILITY_CLASSES",
    "CapabilityRegistry",
    "get_capability_registry",
]
