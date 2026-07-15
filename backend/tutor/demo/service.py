"""Demo scenario loading service."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from tutor.demo.schema import (
    AgentTraceEvent,
    DemoLoadRequest,
    DemoLoadResult,
    DemoScenario,
)
from tutor.services.config.settings import get_settings
from tutor.services.learner_profile.schema import LearnerProfile
from tutor.services.learner_profile.store import get_profile_store
from tutor.services.learning_events.schema import LearningEvent
from tutor.services.learning_events.store import get_learning_event_store
from tutor.services.resource_package.schema import ResourcePackage
from tutor.services.resource_package.store import get_resource_package_store


class DemoScenarioNotFound(KeyError):
    """Raised when a requested demo scenario id does not exist."""


class DemoService:
    """Load deterministic competition scenarios from JSON fixtures."""

    def __init__(self, scenario_dir: Path | None = None) -> None:
        self.scenario_dir = scenario_dir or Path(__file__).resolve().parent / "scenarios"

    def list_scenarios(self) -> list[DemoScenario]:
        """Return all available scenario cards sorted by title."""
        scenarios = [
            DemoScenario.model_validate(self._load_raw(path)["scenario"])
            for path in self.scenario_dir.glob("*.json")
        ]
        return sorted(scenarios, key=lambda s: s.title)

    async def load_scenario(
        self,
        scenario_id: str,
        request: DemoLoadRequest | None = None,
    ) -> DemoLoadResult:
        """Load a scenario and optionally persist it to the normal stores."""
        req = request or DemoLoadRequest()
        raw = self._load_by_id(scenario_id)
        scenario = DemoScenario.model_validate(raw["scenario"])
        user_id = req.user_id or raw.get("user_id") or "competition-demo"
        session_id = req.session_id or raw.get("session_id") or f"demo-{scenario.id}"

        profile = LearnerProfile.model_validate(
            {
                **raw["profile"],
                "user_id": user_id,
            }
        )
        package = ResourcePackage.model_validate(raw["package"])
        package.metadata.update(
            {
                "user_id": user_id,
                "session_id": session_id,
                "demo_scenario_id": scenario.id,
                "source": "competition_demo",
            }
        )

        if req.persist:
            await self._persist_snapshot(
                user_id=user_id,
                profile=profile,
                package=package,
                events=raw.get("events") or [],
            )

        return DemoLoadResult(
            scenario=scenario,
            user_id=user_id,
            session_id=session_id,
            profile=self._frontend_profile(profile),
            path=raw["path"],
            package=package.model_dump(mode="json"),
            assessment=raw["assessment"],
            strategy=raw["strategy"],
            agent_trace=[
                AgentTraceEvent.model_validate(item)
                for item in raw.get("agent_trace", [])
            ],
            learning_loop=list(raw.get("learning_loop", [])),
            teacher_panel=dict(raw.get("teacher_panel", {})),
            runtime_warnings=self._runtime_warnings(),
            live_prompt=scenario.live_prompt,
        )

    async def _persist_snapshot(
        self,
        *,
        user_id: str,
        profile: LearnerProfile,
        package: ResourcePackage,
        events: list[dict[str, Any]],
    ) -> None:
        profile_store = get_profile_store()
        await profile_store.init()
        await profile_store.replace(profile, source="competition_demo")

        package_store = get_resource_package_store()
        await package_store.init()
        await package_store.save(package, user_id=user_id)

        if events:
            event_store = get_learning_event_store()
            await event_store.init()
            learning_events = [
                LearningEvent.from_dict({**event, "user_id": user_id})
                for event in events
            ]
            await event_store.record_many(learning_events)

    def _runtime_warnings(self) -> list[str]:
        settings = get_settings()
        warnings: list[str] = []
        if settings.llm_provider == "deepseek":
            warnings.append(
                "DeepSeek is configured as the LLM provider. Embedding still needs a separate provider/key for vector indexing."
            )
        if not settings.embed_api_key and settings.embed_provider not in {"ollama"}:
            warnings.append(
                "Embedding API key is not configured. Knowledge-base ingestion may fall back or fail depending on the provider settings."
            )
        return warnings

    @staticmethod
    def _frontend_profile(profile: LearnerProfile) -> dict[str, Any]:
        """Return the learner profile in the shape consumed by the UI.

        The backend domain model stores nested objects such as
        ``knowledge_map.scores`` and ``learning_pace``. The current
        frontend panels expect flattened summary fields plus ``pace`` and
        a concept-score map, so the demo endpoint exposes that view while
        keeping the raw nested values for debugging/export.
        """
        raw = profile.model_dump(mode="json")
        summary = profile.to_summary()
        return {
            **summary,
            "knowledge_map": dict(profile.knowledge_map.scores),
            "modality": profile.modality.model_dump(mode="json"),
            "pace": profile.learning_pace.model_dump(mode="json"),
            "learning_pace": raw.get("learning_pace", {}),
            "motivation": profile.motivation.model_dump(mode="json"),
            "error_patterns": raw.get("error_patterns", []),
            "metadata": raw.get("metadata", {}),
            "created_at": raw.get("created_at"),
        }

    def _load_by_id(self, scenario_id: str) -> dict[str, Any]:
        for path in self.scenario_dir.glob("*.json"):
            raw = self._load_raw(path)
            if raw.get("scenario", {}).get("id") == scenario_id:
                return raw
        raise DemoScenarioNotFound(scenario_id)

    @staticmethod
    def _load_raw(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)


@lru_cache(maxsize=1)
def get_demo_service() -> DemoService:
    return DemoService()


__all__ = ["DemoScenarioNotFound", "DemoService", "get_demo_service"]
