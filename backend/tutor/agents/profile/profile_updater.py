"""ProfileUpdaterAgent — apply incremental updates to the stored profile.

Responsibilities
----------------
- Pull the current profile (via ProfileBuilder).
- Optionally call the LLM to *interpret* raw signals (test result, time
  spent, etc.) into a :class:`ProfileDiff`.
- Apply the diff via :meth:`ProfileBuilder.merge_diffs` (or
  :meth:`ProfileBuilder.ingest_signal` for dialogue).
- Return the new profile snapshot.

The agent is *idempotent* — re-running it with the same input is safe.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from tutor.agents.base_agent import BaseAgent
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.learner_profile.builder import (
    DialogueSignal,
    ProfileBuilder,
    get_profile_builder,
)
from tutor.services.learner_profile.schema import LearnerProfile, ProfileDiff


class ProfileUpdaterAgent(BaseAgent):
    """Apply incremental updates to the stored profile."""

    module_name = "profile"
    agent_name = "profile_updater"
    default_temperature = 0.2
    default_max_tokens = 1024

    async def process(
        self,
        context: UnifiedContext,
        stream: StreamBus | None = None,
    ) -> LearnerProfile:
        """Update the profile for ``context.user_id`` from signals in metadata.

        Expected inputs (any of, in ``context.metadata``):

        - ``profile_signal`` : :class:`DialogueSignal`  (from FeatureExtractor)
        - ``exercise_results`` : list of :class:`ExerciseResult`
        - ``profile_diff`` : :class:`ProfileDiff`  (raw pre-built diff)
        """
        builder: ProfileBuilder = get_profile_builder()
        user_id = context.user_id

        diffs_to_apply: list[ProfileDiff] = []
        sources: list[str] = []

        # 1. Dialogue signal (preferred path)
        signal = context.metadata.get("profile_signal")
        if isinstance(signal, DialogueSignal):
            d = signal.to_diff()
            if not d.is_empty():
                diffs_to_apply.append(d)
                sources.append(f"signal:{signal.confidence:.2f}")

        # 2. Exercise results
        results = context.metadata.get("exercise_results") or []
        if results:
            from tutor.services.learner_profile.builder import ExerciseResult

            for r in results:
                if isinstance(r, ExerciseResult):
                    d = r.to_diff()
                    if not d.is_empty():
                        diffs_to_apply.append(d)
                        sources.append(f"exercise:{r.concept}")

        # 3. Pre-built diff (advanced use)
        raw_diff = context.metadata.get("profile_diff")
        if isinstance(raw_diff, ProfileDiff) and not raw_diff.is_empty():
            diffs_to_apply.append(raw_diff)
            sources.append("manual")

        if not diffs_to_apply:
            # Nothing to do — return current state
            return await builder.get(user_id)

        if stream is not None:
            async with stream.stage("profile_update", source=self.agent_name):
                await stream.thinking(
                    f"应用 {len(diffs_to_apply)} 个增量更新...",
                    source=self.agent_name,
                    stage="profile_update",
                    metadata={"sources": sources},
                )
                profile = await builder.merge_diffs(
                    user_id,
                    diffs_to_apply,
                    source=",".join(sources),
                )
                await stream.observation(
                    f"画像已更新到 v{profile.version}",
                    source=self.agent_name,
                    stage="profile_update",
                    metadata={"summary": profile.to_summary()},
                )
        else:
            profile = await builder.merge_diffs(
                user_id,
                diffs_to_apply,
                source=",".join(sources),
            )

        # Stash the new profile back into context for downstream agents
        context.metadata["learner_profile"] = profile
        return profile


__all__ = ["ProfileUpdaterAgent"]
