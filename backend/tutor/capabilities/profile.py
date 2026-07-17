"""LearnerProfileCapability — dialogue-driven profile construction (Phase 2).

Flow per turn
-------------
1. **Load current profile** (or create blank).
2. **Decide mode** based on profile age & coverage:
   - Cold start (no profile or stale > 7d): full extraction + diagnostics
   - Warm (recent profile): incremental update only
3. **Run FeatureExtractorAgent** → :class:`DialogueSignal`
4. **Run ProfileUpdaterAgent** → apply diff, persist
5. If confidence < 0.7 *and* we have weak concepts: run
   **CognitiveDiagnosticAgent** → emit probe questions back to user.
6. Emit a ``result`` with the updated profile + (optional) probe questions.
"""

from __future__ import annotations

from typing import Any

from tutor.agents.profile.cognitive_diagnostic import CognitiveDiagnosticAgent
from tutor.agents.profile.feature_extractor import FeatureExtractorAgent
from tutor.agents.profile.profile_updater import ProfileUpdaterAgent
from tutor.capabilities.failure_reporting import report_degraded
from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.capability_result import CapabilityResult
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile.builder import (
    ProfileBuilder,
    get_profile_builder,
)
from tutor.services.learner_profile.schema import empty_profile


class LearnerProfileCapability(BaseCapability):
    """Dialogue-driven learner profile construction."""

    manifest = CapabilityManifest(
        name="profile",
        description="通过对话构建并更新学习者画像（≥6 维度）",
        stages=[
            "load_profile",
            "decide_mode",
            "feature_extraction",
            "profile_update",
            "diagnostic_probing",
        ],
        tools_used=[],
        cli_aliases=["profile", "learner"],
        tags=["profile", "personalization"],
    )

    # Heuristic: if profile is older than this, treat as cold start
    COLD_START_AGE_DAYS = 7.0

    def __init__(
        self,
        *,
        builder: ProfileBuilder | None = None,
        feature_extractor: FeatureExtractorAgent | None = None,
        profile_updater: ProfileUpdaterAgent | None = None,
        cognitive_diagnostic: CognitiveDiagnosticAgent | None = None,
    ) -> None:
        super().__init__()
        self.builder = builder
        self._owns_builder = builder is None
        self.feature_extractor = feature_extractor or FeatureExtractorAgent()
        self.profile_updater = profile_updater or ProfileUpdaterAgent()
        self.cognitive_diagnostic = cognitive_diagnostic or CognitiveDiagnosticAgent()

    @property
    def _builder(self) -> ProfileBuilder:
        if self.builder is None:
            self.builder = get_profile_builder()
        return self.builder

    async def run(self, context: UnifiedContext, stream: StreamBus) -> CapabilityResult:
        user_id = context.user_id

        # ------------------------------------------------------------------
        # Stage 1: Load current profile
        # ------------------------------------------------------------------
        async with stream.stage("load_profile", source="profile_capability"):
            await stream.thinking(
                f"加载用户 {user_id} 的画像...",
                source="profile_capability",
                stage="load_profile",
            )
            try:
                profile = await self._builder.get(user_id)
            except Exception:  # noqa: BLE001
                await report_degraded(
                    stream,
                    code="PROFILE_LOAD_FAILED",
                    summary="加载画像失败，已使用空画像",
                    source="profile_capability",
                    stage="load_profile",
                )
                profile = empty_profile(user_id=user_id)
            context.metadata["learner_profile"] = profile
            await stream.observation(
                f"当前画像: v{profile.version}, "
                f"{len(profile.knowledge_map.scores)} 概念, "
                f"avg_mastery={profile.knowledge_map.average_mastery():.2f}",
                source="profile_capability",
                stage="load_profile",
            )

        # ------------------------------------------------------------------
        # Stage 2: Decide mode
        # ------------------------------------------------------------------
        async with stream.stage("decide_mode", source="profile_capability"):
            is_cold_start = (
                profile.version <= 1
                and len(profile.knowledge_map.scores) == 0
            ) or len(profile.knowledge_map.scores) == 0
            mode = "cold_start" if is_cold_start else "incremental"
            await stream.observation(
                f"模式: {mode}",
                source="profile_capability",
                stage="decide_mode",
                metadata={"mode": mode, "age_days": profile.age_days()},
            )

        # ------------------------------------------------------------------
        # Stage 3: Feature extraction
        # ------------------------------------------------------------------
        signal = None
        try:
            signal = await self.feature_extractor.process(context, stream=stream)
        except Exception:  # noqa: BLE001
            await report_degraded(
                stream,
                code="PROFILE_FEATURE_EXTRACTION_FAILED",
                summary="特征抽取失败，已跳过本次信号",
                source="profile_capability",
                stage="feature_extraction",
            )

        if signal is not None:
            context.metadata["profile_signal"] = signal

        # ------------------------------------------------------------------
        # Stage 4: Profile update
        # ------------------------------------------------------------------
        try:
            updated_profile = await self.profile_updater.process(context, stream=stream)
        except Exception:  # noqa: BLE001
            await report_degraded(
                stream,
                code="PROFILE_UPDATE_FAILED",
                summary="画像更新失败，已保留原画像",
                source="profile_capability",
                stage="profile_update",
            )
            updated_profile = profile

        # ------------------------------------------------------------------
        # Stage 5: Diagnostic probing (only if confidence low or cold start)
        # ------------------------------------------------------------------
        probe_questions: list[dict[str, Any]] = []
        should_probe = (
            mode == "cold_start"
            or (signal is not None and signal.confidence < 0.7)
            or len(updated_profile.weak_concepts()) >= 2
        )

        if should_probe:
            try:
                probe_questions = await self.cognitive_diagnostic.process(
                    context, stream=stream
                )
            except Exception:  # noqa: BLE001
                await report_degraded(
                    stream,
                    code="PROFILE_DIAGNOSTIC_FAILED",
                    summary="诊断问题生成失败",
                    source="profile_capability",
                    stage="diagnostic_probing",
                )

        # ------------------------------------------------------------------
        # Emit final result
        # ------------------------------------------------------------------
        payload = {
                "user_id": user_id,
                "mode": mode,
                "profile": updated_profile.to_summary(),
                "knowledge_scores": dict(updated_profile.knowledge_map.scores),
                "event_watermark": updated_profile.event_watermark,
                "probe_questions": probe_questions,
                "next_step": (
                    "answer_probe_questions"
                    if probe_questions
                    else "ready_for_resource_generation"
                ),
            }
        return CapabilityResult(
            assistant_message="学习者画像已更新",
            payload=payload,
        )


__all__ = ["LearnerProfileCapability"]
