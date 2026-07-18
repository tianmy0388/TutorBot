"""Demo scenario loading service."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from tutor.demo.schema import (
    AgentTraceEvent,
    DemoCheckpointRequest,
    DemoCheckpointResult,
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
from tutor.services.jobs import JobSubmit, get_job_runner
from tutor.services.learner_profile.builder import ExerciseResult, get_profile_builder


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

        if req.persist or req.mode == "live":
            await self._persist_snapshot(
                user_id=user_id,
                profile=profile,
                package=package,
                events=raw.get("events") or [],
            )

        live_job_id = ""
        live_job_status = ""
        if req.mode == "live":
            live_job = await self._submit_live_job(
                scenario=scenario,
                user_id=user_id,
                session_id=session_id,
            )
            live_job_id = live_job.job_id
            live_job_status = live_job.status.value

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
            mode=req.mode,
            live_job_id=live_job_id,
            live_job_status=live_job_status,
            checkpoint=dict(raw.get("checkpoint", {})),
        )

    async def submit_checkpoint(
        self,
        scenario_id: str,
        request: DemoCheckpointRequest,
    ) -> DemoCheckpointResult:
        raw = self._load_by_id(scenario_id)
        checkpoint = dict(raw.get("checkpoint") or {})
        concept = str(checkpoint.get("concept") or "attention")
        expected = str(checkpoint.get("answer") or "").strip().casefold()
        correct = request.answer.strip().casefold() == expected

        builder = get_profile_builder()
        profile = await builder.get(request.user_id)
        previous_mastery = float(profile.knowledge_map.scores.get(concept, 0.0))
        updated, _ = await builder.ingest_exercise(
            request.user_id,
            ExerciseResult(
                concept=concept,
                correct=correct,
                difficulty=int(checkpoint.get("difficulty") or 3),
                elapsed_seconds=request.elapsed_seconds,
                mistake_type=None if correct else "conceptual_misunderstanding",
                note=f"competition checkpoint answer={request.answer}",
            ),
        )
        updated_mastery = float(updated.knowledge_map.scores.get(concept, 0.0))
        course = str((raw.get("scenario") or {}).get("course") or "")
        if course == "computer_network":
            if correct:
                recommendation = "掌握度提升，下一步进入 Wireshark 抓包实践，观察 Flags、Seq 和 Ack。"
                next_path_node = "wireshark"
            else:
                recommendation = "继续复盘 TCP 三次握手时序图，并重做确认号专项练习。"
                next_path_node = concept
        elif correct:
            recommendation = "掌握度提升，下一步进入 Transformer 编码器结构。"
            next_path_node = "transformer"
        else:
            recommendation = "继续学习注意力机制，并重做 Q/K/V 代码实验。"
            next_path_node = concept

        return DemoCheckpointResult(
            correct=correct,
            concept=concept,
            previous_mastery=previous_mastery,
            updated_mastery=updated_mastery,
            profile_version=updated.version,
            recommendation=recommendation,
            next_path_node=next_path_node,
        )

    async def _submit_live_job(
        self,
        *,
        scenario: DemoScenario,
        user_id: str,
        session_id: str,
    ):
        runner = get_job_runner()
        return await runner.submit(
            JobSubmit(
                user_id=user_id,
                session_id=session_id,
                message=scenario.live_prompt,
                capability="resource_generation",
                language="zh",
                metadata={
                    "course": scenario.course,
                    "knowledge_base_id": scenario.course,
                    "retrieval_scope": f"course:{scenario.course}",
                    "rag_enabled": True,
                    "demo_scenario_id": scenario.id,
                    "selected_resource_types": [
                        "document",
                        "mindmap",
                        "exercise",
                        "reading",
                        "code",
                    ],
                },
            )
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
                "DeepSeek 当前仅作为大模型服务；向量检索仍需单独配置 Embedding 服务。"
            )
        if not settings.embed_api_key and settings.embed_provider not in {"ollama"}:
            warnings.append(
                "尚未配置 Embedding 密钥，知识库向量化与语义检索可能降级。"
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
