"""ResourceGenerationCapability — multi-agent pipeline that emits a full
:class:`ResourcePackage` for one learner + one topic.

Pipeline (per idea.md):

    1. intent_understanding     → Intent(topic, scope, types)
    2. profile_loading         → LearnerProfile snapshot
    3. knowledge_graph_query   → recommended path + concept node
    4. resource_planning       → final list of (type, params)
    5. parallel_generation     → ContentExpert → Pedagogy
                                ├→ Multimedia
                                ├→ ExerciseGenerator
                                ├→ ManimVideo
                                └→ CodeSandbox
    6. quality_review          → per-resource verdict + quality_score
    7. package_assembly        → ResourcePackage
    8. path_integration        → KG PlannedPath attached
    9. result_handoff          → CapabilityResult returned to JobRunner

Each Agent emits its own stage events; the capability emits high-level
stage_start / stage_end wrappers around each pipeline stage.

Errors are contained per-stage: a failure in one branch doesn't kill
the whole generation. The package will simply have one fewer resource.
"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath
from typing import Any

from loguru import logger

from tutor.agents.resource.code_sandbox import CodeSandboxAgent
from tutor.agents.resource.content_expert import ContentExpertAgent
from tutor.agents.resource.exercise_generator import ExerciseGeneratorAgent
from tutor.agents.resource.intent_understanding import (
    Intent,
    IntentUnderstandingAgent,
    parse_intent_keyword,
)
from tutor.agents.resource.manim_video import ManimVideoAgent
from tutor.agents.resource.multimedia import MultimediaAgent
from tutor.agents.resource.pedagogy import PedagogyAgent
from tutor.agents.resource.ppt_generator import PPTGeneratorAgent
from tutor.agents.resource.quality_reviewer import QualityReviewerAgent
from tutor.agents.safety.anti_hallucination import (
    AntiHallucinationAgent,
    OverallVerdict,
)
from tutor.capabilities.failure_reporting import log_degraded, report_degraded
from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.capability_result import CapabilityResult, FollowUpTaskSpec
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.knowledge_graph.service import (
    get_knowledge_graph_service,
)
from tutor.services.learner_profile.builder import (
    ProfileBuilder,
    get_profile_builder,
)
from tutor.services.resource_package.schema import (
    ArtifactRef,
    Resource,
    ResourcePackage,
    ResourceReview,
    ResourceType,
    ReviewVerdict,
)
from tutor.services.resource_package.store import (
    ResourcePackageStore,
    get_resource_package_store,
)


class ResourceGenerationCapability(BaseCapability):
    """Generate a full personalized ResourcePackage."""

    manifest = CapabilityManifest(
        name="resource_generation",
        description="多智能体协同生成 ≥6 类个性化学习资源（核心能力）",
        stages=[
            "intent_understanding",
            "profile_loading",
            "knowledge_graph_query",
            "resource_planning",
            "content_and_pedagogy",
            "parallel_resource_generation",
            "quality_review",
            "anti_hallucination",
            "package_assembly",
            "path_integration",
            "persistence",
        ],
        tools_used=["rag", "web_search"],
        cli_aliases=["resource", "learn", "study"],
        tags=["resource", "generation", "core"],
    )

    def __init__(
        self,
        *,
        builder: ProfileBuilder | None = None,
        intent_agent: IntentUnderstandingAgent | None = None,
        content_expert: ContentExpertAgent | None = None,
        pedagogy: PedagogyAgent | None = None,
        multimedia: MultimediaAgent | None = None,
        exercise_generator: ExerciseGeneratorAgent | None = None,
        manim_video: ManimVideoAgent | None = None,
        code_sandbox: CodeSandboxAgent | None = None,
        quality_reviewer: QualityReviewerAgent | None = None,
        anti_hallucination: AntiHallucinationAgent | None = None,
        ppt_generator: PPTGeneratorAgent | None = None,
        package_store: ResourcePackageStore | None = None,
    ) -> None:
        super().__init__()
        self.builder = builder
        self._owns_builder = builder is None
        self.intent_agent = intent_agent or IntentUnderstandingAgent()
        self.content_expert = content_expert or ContentExpertAgent()
        self.pedagogy = pedagogy or PedagogyAgent()
        self.multimedia = multimedia or MultimediaAgent()
        self.exercise_generator = exercise_generator or ExerciseGeneratorAgent()
        self.manim_video = manim_video or ManimVideoAgent()
        self.code_sandbox = code_sandbox or CodeSandboxAgent()
        self.quality_reviewer = quality_reviewer or QualityReviewerAgent()
        self.anti_hallucination = anti_hallucination or AntiHallucinationAgent()
        self.ppt_generator = ppt_generator or PPTGeneratorAgent()
        self.package_store = package_store
    @property
    def _builder(self) -> ProfileBuilder:
        if self.builder is None:
            self.builder = get_profile_builder()
        return self.builder

    @property
    def _store(self) -> ResourcePackageStore:
        if self.package_store is None:
            self.package_store = get_resource_package_store()
        return self.package_store

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    async def run(self, context: UnifiedContext, stream: StreamBus) -> CapabilityResult:
        profile_snapshot: dict[str, Any] = {}
        intent: Intent | None = None
        resources: list[Resource] = []
        reviews: list[ResourceReview] = []

        # ------------------------------------------------------------------
        # Stage 1: Intent understanding
        # ------------------------------------------------------------------
        async with stream.stage("intent_understanding", source="resource_capability"):
            await stream.thinking(
                "解析用户意图...", source="resource_capability"
            )
            try:
                intent = await self.intent_agent.process(context, stream=stream)
            except Exception:  # noqa: BLE001
                await report_degraded(
                    stream,
                    code="RESOURCE_INTENT_FAILED",
                    summary="意图解析失败，已使用本地规则回退",
                    source="resource_capability",
                    stage="intent_understanding",
                )
                intent = parse_intent_keyword(context.user_message)

        # ------------------------------------------------------------------
        # Stage 2: Profile loading
        # ------------------------------------------------------------------
        async with stream.stage("profile_loading", source="resource_capability"):
            await stream.thinking(
                "加载学习者画像...", source="resource_capability"
            )
            try:
                profile = await self._builder.get(context.user_id)
                profile_snapshot = profile.to_summary()
                context.metadata["learner_profile"] = profile
            except Exception:  # noqa: BLE001
                await report_degraded(
                    stream,
                    code="RESOURCE_PROFILE_LOAD_FAILED",
                    summary="画像加载失败，将使用默认生成策略",
                    source="resource_capability",
                    stage="profile_loading",
                )
                profile_snapshot = {}

        if intent is None:
            intent = parse_intent_keyword(context.user_message)

        # ------------------------------------------------------------------
        # Stage 3: Knowledge graph query
        # ------------------------------------------------------------------
        kg_summary: dict[str, Any] = {}
        async with stream.stage("knowledge_graph_query", source="resource_capability"):
            try:
                svc = get_knowledge_graph_service()
                course = (
                    context.metadata.get("course")
                    or svc.default_course()
                )
                if course and svc.has_course(course):
                    from tutor.services.learner_profile.schema import LearnerProfile

                    prof_obj = (
                        profile
                        if isinstance(profile, LearnerProfile)
                        else LearnerProfile()
                    )
                    locate = svc.locate(course, prof_obj)
                    kg_summary = {
                        "course": course,
                        "mastered_count": len(locate["mastered"]),
                        "unmastered_count": len(locate["unmastered"]),
                        "next_targets": locate["next_targets"][:5],
                    }
                    await stream.observation(
                        f"知识图谱定位：掌握 {len(locate['mastered'])}，"
                        f"未掌握 {len(locate['unmastered'])}",
                        source="resource_capability",
                        stage="knowledge_graph_query",
                        metadata=kg_summary,
                    )
            except Exception:  # noqa: BLE001
                await report_degraded(
                    stream,
                    code="RESOURCE_KNOWLEDGE_GRAPH_FAILED",
                    summary="知识图谱查询失败，已跳过图谱增强",
                    source="resource_capability",
                    stage="knowledge_graph_query",
                )

        # ------------------------------------------------------------------
        # Stage 4: Resource planning
        # ------------------------------------------------------------------
        async with stream.stage("resource_planning", source="resource_capability"):
            planned_types = self._plan_resources(
                intent=intent,
                profile_snapshot=profile_snapshot,
                kg_summary=kg_summary,
                metadata=dict(context.metadata or {}),
            )
            await stream.observation(
                f"计划生成 {len(planned_types)} 类资源："
                f"{', '.join(t.value for t in planned_types)}",
                source="resource_capability",
                stage="resource_planning",
                metadata={"types": [t.value for t in planned_types]},
            )

        # ------------------------------------------------------------------
        # Stage 5: Content + Pedagogy (sequential dependency)
        # ------------------------------------------------------------------
        document_resource: Resource | None = None
        pedagogy_resource: Resource | None = None
        async with stream.stage(
            "content_and_pedagogy", source="resource_capability"
        ):
            if ResourceType.DOCUMENT in planned_types:
                try:
                    document_resource = await self.content_expert.process(
                        context,
                        stream=stream,
                        topic=intent.topic,
                        profile=profile_snapshot,
                    )
                    # Pedagogy rewrites ContentExpert output
                    pedagogy_resource = await self.pedagogy.process(
                        context,
                        stream=stream,
                        source_resource=document_resource,
                        profile=profile_snapshot,
                    )
                    # Bump confidence on the teaching version
                    pedagogy_resource.confidence_score = max(
                        pedagogy_resource.confidence_score,
                        document_resource.confidence_score,
                    )
                    # **2026-07-08 fix (187b2955):** emit a ``RESOURCE``
                    # event for the pedagogy output *before* the slower
                    # parallel agents + video rendering finish. The
                    # frontend renders the document card immediately.
                    try:
                        await stream.resource(
                            pedagogy_resource,
                            source="resource_capability",
                            stage="content_and_pedagogy",
                        )
                    except Exception:  # noqa: BLE001
                        log_degraded(
                            code="RESOURCE_STREAM_EMIT_FAILED",
                            source="resource_capability",
                            stage="content_and_pedagogy",
                        )
                except Exception:  # noqa: BLE001
                    await report_degraded(
                        stream,
                        code="RESOURCE_CONTENT_GENERATION_FAILED",
                        summary="内容生成失败",
                        source="resource_capability",
                        stage="content_and_pedagogy",
                    )
            else:
                await stream.observation(
                    "未计划 document 类型，跳过内容生成",
                    source="resource_capability",
                )

        # ------------------------------------------------------------------
        # Stage 6: Parallel resource generation (mindmap/exercise/video/code)
        # ------------------------------------------------------------------
        async with stream.stage(
            "parallel_resource_generation", source="resource_capability"
        ):
            parallel_resources = await self._generate_parallel(
                context=context,
                intent=intent,
                profile_snapshot=profile_snapshot,
                source_content=pedagogy_resource.content if pedagogy_resource else "",
                planned_types=planned_types,
                stream=stream,
            )
            resources.extend(parallel_resources)

        # ------------------------------------------------------------------
        # Stage 7: Quality review (parallel per resource)
        # ------------------------------------------------------------------
        all_resources: list[Resource] = []
        if pedagogy_resource is not None:
            all_resources.append(pedagogy_resource)
        if document_resource is not None and document_resource is not pedagogy_resource:
            # Keep pedagogy version (it supersedes); document is intermediate
            pass
        all_resources.extend(parallel_resources)

        # ------------------------------------------------------------------
        # **2026-07-07 fix:** pre-filter resources whose *generation*
        # already failed (vs. resources whose content is simply
        # low-quality). The agent still returns a typed failed
        # Resource so the user sees a retryable failure in the
        # trace, but it must NOT enter the quality-review loop —
        # the reviewer would correctly reject it, then the reject
        # filter would strip it from the package, then the
        # video_rendering stage would be a confusing no-op.
        #
        # Filter rule:
        #   * video     — drop if ``render_status == "failed"``
        #                  (Manim code generation / syntax check failed)
        #   * code      — keep; reviewer handles ``execution_status``
        #                  failures so we don't lose valid-but-env-broken
        #                  snippets.
        #   * other     — drop only when ``format_specific.failure`` is
        #                  present (for example, a failed PPT render).
        #
        # We emit a clear stream observation so the UI / chat
        # channel can show "1 video resource skipped (generation
        # failed)" rather than a silent 5/6 retain count.
        # ------------------------------------------------------------------
        all_resources, prefilter_summary = await self._prefilter_failed_resources(
            all_resources, stream
        )

        async with stream.stage("quality_review", source="resource_capability"):
            reviews = await self._review_all(all_resources, context, stream)

        # ------------------------------------------------------------------
        # Stage 8: Anti-hallucination + Safety (per-resource)
        # ------------------------------------------------------------------
        safety_reports: list[Any] = []
        async with stream.stage("anti_hallucination", source="resource_capability"):
            safety_reports = await self._safety_check_all(
                all_resources, context, intent, stream
            )

        # ------------------------------------------------------------------
        # Stage 9: Package assembly
        # ------------------------------------------------------------------
        package = ResourcePackage(
            topic=intent.topic,
            resources=all_resources,
            target_profile_snapshot=profile_snapshot,
            learning_path_summary=kg_summary,
            generated_by=[
                self.intent_agent.agent_name,
                self.content_expert.agent_name,
                self.pedagogy.agent_name,
                self.multimedia.agent_name,
                self.exercise_generator.agent_name,
                self.manim_video.agent_name,
                self.code_sandbox.agent_name,
                self.quality_reviewer.agent_name,
                self.anti_hallucination.agent_name,
                self.ppt_generator.agent_name,
            ],
            metadata={
                "intent_scope": intent.scope,
                "intent_confidence": intent.confidence,
                "intent_prerequisites": intent.prerequisites,
                "review_count": len(reviews),
                "passing_reviews": sum(
                    1 for r in reviews if r.verdict == ReviewVerdict.PASS
                ),
                "safety_blocked": sum(
                    1 for s in safety_reports if s.overall_verdict == OverallVerdict.UNSAFE
                ),
            },
        )
        package.associate_originating_job(context.job_id)
        for resource in package.resources:
            resource.metadata.setdefault("package_id", package.package_id)
        # Attach review + safety to each resource
        review_by_id = {r.resource_id: r for r in reviews}
        # Match safety to resource by order (same iteration order as resources)
        for idx, r in enumerate(package.resources):
            rev = review_by_id.get(r.resource_id)
            if rev is not None:
                r.metadata["review"] = {
                    "verdict": rev.verdict.value,
                    "quality_score": rev.quality_score,
                    "issues": rev.issues,
                    "suggestions": rev.suggestions,
                }
            if idx < len(safety_reports):
                safety = safety_reports[idx]
                r.metadata["safety"] = safety.to_dict()

        # **2026-07-08 fix (187b2955):** drop resources whose safety
        # verdict is ``UNSAFE`` (refuted claims OR content-safety
        # violation). Before this, ``safety_blocked`` was counted in
        # metadata but the unsafe resource was still shipped to the
        # user — exactly the kind of "the user sees a hallucinated
        # answer as a confident resource card" failure we don't want.
        # We keep ``CAUTION`` and ``UNVERIFIED`` (those are educational
        # signals, not blocks).
        unsafe_ids: set[str] = set()
        for idx, r in enumerate(package.resources):
            if idx >= len(safety_reports):
                continue
            sv = safety_reports[idx].overall_verdict
            if sv == OverallVerdict.UNSAFE:
                unsafe_ids.add(r.resource_id)
        if unsafe_ids:
            before_count = len(package.resources)
            package.resources = [
                r for r in package.resources if r.resource_id not in unsafe_ids
            ]
            package.metadata.setdefault("filtered_safety", []).extend(
                [
                    {
                        "resource_id": rid,
                        "reason": "anti_hallucination_unsafe",
                    }
                    for rid in unsafe_ids
                ]
            )
            await stream.observation(
                f"已过滤 {len(unsafe_ids)} 个安全校验未通过的资源"
                f"（保留 {len(package.resources)}/{before_count}）",
                source="resource_capability",
                stage="anti_hallucination",
                metadata={
                    "unsafe_count": len(unsafe_ids),
                    "kept_count": len(package.resources),
                },
            )
            logger.warning(
                f"resource_capability: filtered {len(unsafe_ids)} unsafe resources "
                f"(topic={package.topic!r}); kept={len(package.resources)}"
            )

        # ------------------------------------------------------------------
        # **2026-06-22 fix (Task 9):** filter out resources whose quality
        # review verdict is ``reject`` BEFORE we persist or surface the
        # package. Previously the verdict was attached as metadata but
        # the resource still shipped to the chat viewer — so a code
        # resource with empty code or a video resource whose generation
        # failed was published to the user as a real, usable resource.
        #
        # We keep ``REVISE`` (the LLM thinks it can be improved but is
        # still usable) and ``PASS``; only ``REJECT`` is dropped.
        # Dropped resources are recorded in ``package.metadata`` for
        # downstream observability and surfaced as a stream observation
        # so the chat UI can show "2 resources were filtered".
        # ------------------------------------------------------------------
        rejected_ids = {
            r.resource_id
            for r in package.resources
            if (r.metadata.get("review") or {}).get("verdict") == "reject"
        }
        if rejected_ids:
            before_count = len(package.resources)
            package.resources = [
                r for r in package.resources if r.resource_id not in rejected_ids
            ]
            package.metadata.setdefault("filtered_reviews", []).extend(
                [
                    {
                        "resource_id": rid,
                        "reason": "quality_review_rejected",
                    }
                    for rid in rejected_ids
                ]
            )
            await stream.observation(
                f"已过滤 {len(rejected_ids)} 个质量不达标的资源（保留 "
                f"{len(package.resources)}/{before_count}）",
                source="resource_capability",
                stage="quality_review",
                metadata={
                    "rejected_count": len(rejected_ids),
                    "kept_count": len(package.resources),
                },
            )
            logger.warning(
                f"resource_capability: filtered {len(rejected_ids)} rejected resources "
                f"(topic={package.topic!r}); kept={len(package.resources)}"
            )

        # ------------------------------------------------------------------
        # Stage 10: Path integration — store package ID in profile metadata
        # ------------------------------------------------------------------
        async with stream.stage("path_integration", source="resource_capability"):
            try:
                # Emit summary into profile metadata for next-turn continuity
                profile.metadata.setdefault("resource_history", []).append(
                    {
                        "package_id": package.package_id,
                        "topic": package.topic,
                        "resource_count": len(package.resources),
                        "at": package.created_at.isoformat(),
                    }
                )
                profile.metadata["last_package_id"] = package.package_id
                profile.metadata["last_topic"] = package.topic
                await self._builder.store.replace(profile, source="resource_capability")
            except Exception:  # noqa: BLE001
                log_degraded(
                    code="RESOURCE_PROFILE_METADATA_FAILED",
                    source="resource_capability",
                    stage="path_integration",
                )

        # ------------------------------------------------------------------
        # Stage 11: Persistence — write the package to the persistent store
        # ------------------------------------------------------------------
        # First, move any PPT artifacts generated with a placeholder
        # package_id to the real one. This is a small bookkeeping step
        # so the file layout mirrors the resource_packages DB layout.
        try:
            self._relocate_ppt_artifacts(package)
        except Exception:  # noqa: BLE001
            log_degraded(
                code="RESOURCE_PPT_RELOCATION_FAILED",
                source="resource_capability",
                stage="persistence",
            )
        for resource in package.resources:
            self._canonicalize_resource_artifacts(resource)

        async with stream.stage("persistence", source="resource_capability"):
            try:
                # 2026-06-21 plan: tag the package with the session_id
                # so conversation-detail can filter packages by session
                # in a single SQL query. We write the id into
                # ``package.metadata`` (the store already round-trips
                # it through the ``package_metadata`` JSON column) and
                # the per-resource ``metadata`` for downstream lookups
                # (RAG scope, retried jobs, etc.).
                session_id = getattr(context, "session_id", "") or ""
                if session_id:
                    package.metadata.setdefault("session_id", session_id)
                    for r in package.resources:
                        r.metadata.setdefault("session_id", session_id)
                await self._store.save(package, user_id=context.user_id)
                await stream.observation(
                    f"资源包已持久化: pkg={package.package_id[:12]}… "
                    f"user={context.user_id} resources={len(package.resources)}",
                    source="resource_capability",
                    stage="persistence",
                    metadata={
                        "package_id": package.package_id,
                        "user_id": context.user_id,
                        "resource_count": len(package.resources),
                    },
                )
            except Exception:  # noqa: BLE001
                await report_degraded(
                    stream,
                    code="RESOURCE_PERSIST_FAILED",
                    summary="资源包持久化失败，本轮结果仍可查看",
                    source="resource_capability",
                    stage="persistence",
                )

        follow_up_tasks = self._video_follow_up_specs(package, context.user_id)
        artifact_refs: list[ArtifactRef] = []
        for resource in package.resources:
            fs = resource.format_specific or {}
            artifact_key = fs.get("artifact_key")
            if artifact_key:
                artifact_refs.append(
                    ArtifactRef(
                        name=PurePosixPath(str(artifact_key)).name,
                        kind=resource.type.value,
                        artifact_key=str(artifact_key),
                    )
                )
            for raw in fs.get("artifacts") or []:
                try:
                    ref = ArtifactRef.model_validate(raw)
                except Exception:  # noqa: BLE001
                    continue
                if ref.artifact_key:
                    artifact_refs.append(ref)

        payload = {
                "package": package.model_dump(mode="json"),
                "summary": package.summary(),
                "reviews": [
                    {
                        "resource_id": r.resource_id,
                        "verdict": r.verdict.value,
                        "quality_score": r.quality_score,
                    }
                    for r in reviews
                ],
                "kg_summary": kg_summary,
                "next_step": "open_resource_cards",
            }
        return CapabilityResult(
            assistant_message=f"已生成 {len(package.resources)} 项学习资源",
            payload=payload,
            artifacts=tuple(artifact_refs),
            follow_up_tasks=follow_up_tasks,
        )

# ---------------------------------------------------------------------------
# PPT bookkeeping
# ------------------------------------------------------------------

    def _relocate_ppt_artifacts(self, package: ResourcePackage) -> None:
        """Move any PPT files written under ``ad_hoc/`` to
        ``<data_dir>/ppt/<package_id>/`` and retain only a portable key.
        """
        from pathlib import Path

        from tutor.services.artifacts import resolve_artifact_key, to_artifact_key
        from tutor.services.config.settings import get_settings
        from tutor.services.ppt import get_ppt_service

        data_dir = get_settings().data_dir
        ppt_root = get_ppt_service().output_dir
        for r in package.resources:
            if r.type != ResourceType.PPT:
                continue
            fs = r.format_specific or {}
            artifact_key = fs.get("artifact_key")
            raw = artifact_key or fs.get("pptx_path")
            if not raw:
                continue
            src = (
                resolve_artifact_key(str(raw), data_dir)
                if artifact_key
                else Path(str(raw))
            )
            if not src.exists():
                self._canonicalize_resource_artifacts(r)
                continue
            # Already under the right package dir?
            try:
                if src.parent.parent == ppt_root and src.parent.name == package.package_id:
                    r.format_specific["artifact_key"] = to_artifact_key(src, data_dir)
                    r.format_specific.pop("pptx_path", None)
                    continue
            except Exception:
                pass
            dst_dir = ppt_root / package.package_id
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = dst_dir / src.name
            try:
                if dst.exists():
                    dst.unlink()
                src.rename(dst)
            except OSError:
                # Cross-device or read-only — fall back to copy.
                import shutil

                shutil.copy2(src, dst)
            r.format_specific["artifact_key"] = to_artifact_key(dst, data_dir)
            r.format_specific.pop("pptx_path", None)

    @staticmethod
    def _canonicalize_resource_artifacts(resource: Resource) -> None:
        """Remove host paths from a resource before persistence or streaming."""
        from tutor.services.config.settings import get_settings
        from tutor.services.resource_package.store import portable_format_specific

        resource.format_specific = portable_format_specific(
            resource.format_specific,
            get_settings().data_dir,
        )

    # ------------------------------------------------------------------
    # Video rendering (2026-06-21 plan, C2)
    # ------------------------------------------------------------------

    @staticmethod
    def _video_follow_up_specs(
        package: ResourcePackage,
        user_id: str,
    ) -> tuple[FollowUpTaskSpec, ...]:
        """Build durable, deterministic work specs for pending video renders."""
        return tuple(
            FollowUpTaskSpec(
                kind="video_render",
                payload={
                    "package_id": package.package_id,
                    "resource_id": resource.resource_id,
                    "user_id": user_id,
                },
                dedupe_key=f"video:{package.package_id}:{resource.resource_id}",
            )
            for resource in package.resources
            if resource.type == ResourceType.VIDEO
            and (resource.format_specific or {}).get("render_status") == "pending"
        )

    async def _render_one_video(
        self,
        res: Resource,
        package: ResourcePackage,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> None:
        """Render a single video, updating its ``format_specific`` and
        emitting a ``RESOURCE`` event when done.

        **2026-07-08 fix (fdb26152):** the previous per-render closure
        only emitted ``stream.observation(...)``; the frontend never
        knew the resource had updated ``render_status=ready`` /
        ``video_url`` because no incremental ``RESOURCE`` event was
        sent after the original parallel-generation one. We now emit
        a fresh ``RESOURCE`` event so the right-pane card swaps the
        placeholder for a real video player.
        """
        try:
            from tutor.services.manim_render.service import (
                get_manim_render_service,
            )

            manim_service = get_manim_render_service()
            code = (res.format_specific or {}).get("manim_code", "")
            scene = (res.format_specific or {}).get("scene_class", "GeneratedScene")
            if not code:
                res.format_specific["render_status"] = "failed"
                res.format_specific["render_error"] = "no manim_code in resource"
            else:
                render_result = await manim_service.render(
                    code=code, scene_class=scene
                )
                # Update the resource payload in-place.
                res.format_specific["render_status"] = (
                    "ready" if render_result.success else "failed"
                )
                if render_result.public_url:
                    res.format_specific["video_url"] = render_result.public_url
                if render_result.video_path:
                    from pathlib import Path

                    from tutor.services.artifacts import (
                        UnsafeArtifactKey,
                        to_artifact_key,
                    )
                    from tutor.services.config.settings import get_settings

                    res.format_specific.pop("mp4_path", None)
                    try:
                        res.format_specific["artifact_key"] = to_artifact_key(
                            Path(render_result.video_path),
                            get_settings().data_dir,
                        )
                        res.format_specific.pop("artifact_unresolved", None)
                    except UnsafeArtifactKey:
                        res.format_specific.pop("artifact_key", None)
                        res.format_specific["artifact_unresolved"] = True
                if render_result.duration_seconds:
                    res.format_specific["duration_seconds"] = (
                        render_result.duration_seconds
                    )
                if not render_result.success:
                    res.format_specific["render_error_code"] = "VIDEO_RENDER_FAILED"
                    res.format_specific["render_error"] = "Video rendering failed"
                await stream.observation(
                    (
                        f"视频渲染{'成功' if render_result.success else '失败'}: "
                        f"{res.title}"
                    ),
                    source="resource_capability",
                    stage="video_rendering",
                    metadata={
                        "resource_id": res.resource_id,
                        "success": render_result.success,
                        "attempts": render_result.attempts,
                    },
                )
        except Exception:  # noqa: BLE001
            res.format_specific["render_status"] = "failed"
            res.format_specific["render_error_code"] = "VIDEO_RENDER_FAILED"
            res.format_specific["render_error"] = "Video rendering failed"
            await report_degraded(
                stream,
                code="VIDEO_RENDER_FAILED",
                summary=f"视频渲染失败: {res.title}",
                source="resource_capability",
                stage="video_rendering",
            )
        finally:
            # **2026-07-08 fix:** emit a fresh ``RESOURCE`` event so the
            # frontend swaps the placeholder card for a real video
            # player. We do this in ``finally`` so even render failures
            # surface (the user sees "渲染失败" instead of a forever-
            # pending placeholder).
            try:
                await stream.resource(
                    res,
                    source="resource_capability",
                    stage="video_rendering",
                )
            except Exception:  # noqa: BLE001
                log_degraded(
                    code="RESOURCE_STREAM_EMIT_FAILED",
                    source="resource_capability",
                    stage="video_rendering",
                )
            # Re-save the package so the updated format_specific is
            # persisted for reconnection / reload.
            try:
                await self._store.save(package, user_id=context.user_id)
            except Exception:  # noqa: BLE001
                log_degraded(
                    code="VIDEO_RENDER_PERSIST_FAILED",
                    source="resource_capability",
                    stage="video_rendering",
                )

    # ------------------------------------------------------------------
    # Resource planning
    # ------------------------------------------------------------------

    def _plan_resources(
        self,
        *,
        intent: Intent,
        profile_snapshot: dict[str, Any],
        kg_summary: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> list[ResourceType]:
        """Decide which resource types to generate.

        Strategy:
        1. **Authoritative override**: if the caller (router / retry
           endpoint) put ``selected_resource_types`` in
           ``context.metadata``, that list wins. This is the Task 4
           plan-confirmation contract: the user said yes to exactly
           these types, so we run exactly these types.
        2. Otherwise: start from ``intent.resource_types`` (default
           subset), then apply the legacy heuristics (visual keyword
           detection, modality upranking, scope adjustments, comparison
           drop, document default).
        """
        # 1. Authoritative override from the plan/confirm flow.
        metadata = metadata or {}
        selected = metadata.get("selected_resource_types")
        if selected:
            valid = {rt.value for rt in ResourceType}
            chosen: list[ResourceType] = []
            seen: set[ResourceType] = set()
            for t in selected:
                if t in valid and ResourceType(t) not in seen:
                    chosen.append(ResourceType(t))
                    seen.add(ResourceType(t))
            return chosen

        types = list(intent.resource_types)

        # ------------------------------------------------------------------
        # Decide whether VIDEO makes sense for this turn
        # ------------------------------------------------------------------
        msg = (intent.raw_message or "").lower()
        visual_keywords = (
            "可视化", "动画", "演示", "原理", "推导", "图解", "流程",
            "工作原理", "动图", "示意", "演示", "demonstration",
            "visualize", "animation", "demo", "how it works", "intuition",
        )
        wants_video = any(k in msg for k in visual_keywords) or any(
            k in (intent.topic or "").lower() for k in visual_keywords
        )

        if wants_video and ResourceType.VIDEO not in types:
            types.append(ResourceType.VIDEO)

        # Modality-driven upranking (don't remove; just ensure presence)
        modality = profile_snapshot.get("modality_dominant") or ""
        if modality == "video" and ResourceType.VIDEO not in types:
            types.append(ResourceType.VIDEO)
        if modality == "diagram" and ResourceType.MINDMAP not in types:
            types.append(ResourceType.MINDMAP)
        if modality == "code" and ResourceType.CODE not in types:
            types.append(ResourceType.CODE)
        if modality == "exercise" and ResourceType.EXERCISE not in types:
            types.append(ResourceType.EXERCISE)
        if modality in ("text", "verbal") and ResourceType.READING not in types:
            types.append(ResourceType.READING)

        # Scope adjustments
        if intent.scope == "overview":
            # Drop heavy types for overviews
            types = [t for t in types if t != ResourceType.VIDEO]
            if ResourceType.DOCUMENT not in types:
                types.append(ResourceType.DOCUMENT)
        if intent.scope == "deep_dive":
            # deep_dive is the only place VIDEO is added without an explicit
            # user signal — even then only if the topic is concept-heavy.
            if (
                ResourceType.VIDEO not in types
                and (wants_video or modality == "video")
            ):
                types.append(ResourceType.VIDEO)
            if ResourceType.EXERCISE not in types:
                types.append(ResourceType.EXERCISE)

        # Comparison / ranking / list queries don't need a video — drop it
        # even if the user typed one of the visual keywords by accident.
        comparison_kw = ("对比", "比较", "排行", "排名", "top ", "benchmark",
                         "leaderboard", "comparison", "ranking", " vs ", "versus")
        if any(k in msg for k in comparison_kw):
            types = [t for t in types if t != ResourceType.VIDEO]

        # Always include document unless explicitly excluded
        if (
            ResourceType.DOCUMENT not in types
            and intent.scope != "deep_dive"
            and "document" in msg
        ):
            # Document is optional for deep_dive (video/reading may suffice)
            types.append(ResourceType.DOCUMENT)

        # Deduplicate but preserve order
        seen: set[ResourceType] = set()
        out: list[ResourceType] = []
        for t in types:
            if t in seen:
                continue
            seen.add(t)
            out.append(t)
        return out

    # ------------------------------------------------------------------
    # Parallel generation
    # ------------------------------------------------------------------

    async def _generate_parallel(
        self,
        *,
        context: UnifiedContext,
        intent: Intent,
        profile_snapshot: dict[str, Any],
        source_content: str,
        planned_types: list[ResourceType],
        stream: StreamBus,
    ) -> list[Resource]:
        """Run the type-specific agents in parallel."""
        tasks: list[tuple[ResourceType, asyncio.Task]] = []

        # **2026-07-08 fix (187b2955):** wrap each agent call in a semaphore-
        # bounded task so we don't fan out 6+ concurrent LLM calls if the
        # topic requests many resource types. Before this, the trace showed
        # 5+ LLM calls running in parallel + 4 sequential pedagogy invocations,
        # totalling 670s of LLM time on a 600s budget. The cap is intentional:
        # we still get parallelism (3× faster than serial), but no longer
        # blow through the upstream provider's rate limit or stall on a single
        # slow call. ``Semaphore(0)`` or negative → unbounded (legacy behaviour).
        import os
        try:
            cap = int(os.environ.get("TUTOR_PARALLEL_AGENT_CAP", "3"))
        except ValueError:
            cap = 3
        sem: asyncio.Semaphore | None = (
            asyncio.Semaphore(cap) if cap > 0 else None
        )

        async def _safe(coro: Any, rtype: ResourceType) -> Resource | None:
            try:
                if sem is not None:
                    async with sem:
                        return await coro
                return await coro
            except Exception:  # noqa: BLE001
                await report_degraded(
                    stream,
                    code=f"RESOURCE_{rtype.value.upper()}_GENERATION_FAILED",
                    summary=f"{rtype.value} 生成失败",
                    source="resource_capability",
                    stage="parallel_resource_generation",
                )
                return None

        if ResourceType.MINDMAP in planned_types:
            tasks.append((
                ResourceType.MINDMAP,
                asyncio.create_task(_safe(
                    self.multimedia.process(
                        context,
                        stream=stream,
                        topic=intent.topic,
                        source_content=source_content,
                        profile=profile_snapshot,
                    ),
                    ResourceType.MINDMAP,
                )),
            ))
        if ResourceType.EXERCISE in planned_types:
            tasks.append((
                ResourceType.EXERCISE,
                asyncio.create_task(_safe(
                    self.exercise_generator.process(
                        context,
                        stream=stream,
                        topic=intent.topic,
                        source_content=source_content,
                        profile=profile_snapshot,
                    ),
                    ResourceType.EXERCISE,
                )),
            ))
        if ResourceType.VIDEO in planned_types:
            tasks.append((
                ResourceType.VIDEO,
                asyncio.create_task(_safe(
                    self.manim_video.process(
                        context,
                        stream=stream,
                        topic=intent.topic,
                        source_content=source_content,
                        profile=profile_snapshot,
                    ),
                    ResourceType.VIDEO,
                )),
            ))
        if ResourceType.CODE in planned_types:
            tasks.append((
                ResourceType.CODE,
                asyncio.create_task(_safe(
                    self.code_sandbox.process(
                        context,
                        stream=stream,
                        topic=intent.topic,
                        source_content=source_content,
                        profile=profile_snapshot,
                        run_locally=True,
                    ),
                    ResourceType.CODE,
               )),
            ))
        if ResourceType.READING in planned_types:
            # Reading reuses pedagogy-style content with citations suffix
            tasks.append((
                ResourceType.READING,
                asyncio.create_task(_safe(
                    self._generate_reading(
                        topic=intent.topic,
                        profile_snapshot=profile_snapshot,
                        source_content=source_content,
                        stream=stream,
                    ),
                    ResourceType.READING,
               )),
            ))
        if ResourceType.PPT in planned_types:
            tasks.append((
                ResourceType.PPT,
                asyncio.create_task(_safe(
                    self.ppt_generator.process(
                        topic=intent.topic,
                        source_content=source_content,
                        profile=profile_snapshot,
                        package_id=None,  # filled below once we have it
                        stream=stream,
                    ),
                    ResourceType.PPT,
                )),
            ))

        if not tasks:
            return []

        # **2026-07-08 fix (187b2955):** as each agent finishes, immediately
        # emit a ``RESOURCE`` event so the frontend can render the card
        # BEFORE the whole package assembly / video render / safety check
        # sequence runs. Previously the right pane only updated at the
        # very end (``stream.result(...)``); any later failure left the
        # user with an empty pane even though some resources were already
        # done. ``asyncio.as_completed`` lets us interleave completion
        # events with the gather waiting on the rest.
        finished: list[Resource] = []
        for fut in asyncio.as_completed([t[1] for t in tasks]):
            r = await fut
            if r is None:
                continue
            finished.append(r)
            try:
                self._canonicalize_resource_artifacts(r)
                await stream.resource(
                    r,
                    source="resource_capability",
                    stage="parallel_resource_generation",
                )
            except Exception:  # noqa: BLE001
                # Stream emission must NEVER block the pipeline. A failed
                # event broadcast (closed bus, full queue) must not
                # invalidate an already-finished resource.
                log_degraded(
                    code="RESOURCE_STREAM_EMIT_FAILED",
                    source="resource_capability",
                    stage="parallel_resource_generation",
                )
        return finished

    async def _generate_reading(
        self,
        *,
        topic: str,
        profile_snapshot: dict[str, Any],
        source_content: str,
        stream: StreamBus,
    ) -> Resource:
        """Build a reading resource from the pedagogy output + RAG context.

        Uses PedagogyAgent to rewrite as a 'further reading' piece with
        citations inferred from the source content (no real RAG in MVP).
        """
        from tutor.services.resource_package.schema import (
            ReadingResource,
            build_resource,
        )

        async with stream.stage("reading_compilation", source="reading_compiler"):
            await stream.thinking(
                f"为「{topic}」生成拓展阅读材料...",
                source="reading_compiler",
                stage="reading_compilation",
            )
            # Reuse pedagogy agent but constrain to a reading-style output
            source_resource = Resource(
                type=ResourceType.DOCUMENT,
                title=topic,
                content=source_content[:6000] or f"# {topic}\n\n",
            )
            try:
                improved = await self.pedagogy.process(
                    UnifiedContext(language="zh"),  # minimal context
                    stream=stream,
                    source_resource=source_resource,
                    profile=profile_snapshot,
                )
            except Exception:
                improved = source_resource

        # Build ReadingResource payload
        payload = ReadingResource(
            citations=[],  # RAG integration in Phase 5
            estimated_reading_minutes=max(5, improved.estimated_minutes // 2),
        )

        content = (
            f"# {topic} — 拓展阅读\n\n"
            f"{improved.content}\n\n"
            f"## 推荐资源\n\n"
            f"（Phase 5 将接入 RAG 自动检索相关论文和资料）\n"
        )

        return build_resource(
            type=ResourceType.READING,
            title=f"{topic} — 拓展阅读",
            content=content,
            format_specific=payload.model_dump(),
            difficulty=improved.difficulty,
            estimated_minutes=payload.estimated_reading_minutes,
            prerequisites=[],
            generated_by=["reading_compiler", self.pedagogy.agent_name],
            confidence_score=improved.confidence_score * 0.9,
            topic=topic,
            tags=["reading", "further"],
        )

    # ------------------------------------------------------------------
    # Quality review
    # ------------------------------------------------------------------

    @staticmethod
    def _is_generation_failed(resource: Resource) -> bool:
        """Return True if the resource's *generation* pipeline failed
        (vs. the resource being merely low-quality).

        Video uses ``format_specific.render_status == "failed"``. Other
        generators may return a structured ``format_specific.failure`` when
        no usable artifact exists (for example a failed PPT render).

        Code resources with ``execution_status == "failed"`` are NOT
        filtered here — ``RUNTIME_DEPENDENCY_MISSING`` is a valid
        educational resource that the user can still read; the
        quality reviewer decides.
        """
        fs = resource.format_specific or {}
        if resource.type == ResourceType.CODE:
            return False
        return (
            resource.type == ResourceType.VIDEO
            and fs.get("render_status") == "failed"
        ) or isinstance(fs.get("failure"), dict)

    async def _prefilter_failed_resources(
        self,
        resources: list[Resource],
        stream: StreamBus,
    ) -> tuple[list[Resource], list[dict[str, Any]]]:
        """Drop resources whose *generation* failed (not the content
        quality) before the quality-review loop.

        Returns ``(kept_resources, filtered_summary)`` so the caller
        can attach the summary to ``package.metadata`` for downstream
        observability and so a focused regression test can assert
        exactly which resources were dropped.
        """
        before = len(resources)
        kept: list[Resource] = []
        filtered: list[dict[str, Any]] = []
        for r in resources:
            if self._is_generation_failed(r):
                fs = r.format_specific or {}
                filtered.append(
                    {
                        "resource_id": r.resource_id,
                        "type": r.type.value,
                        "title": r.title,
                        "render_error": fs.get("render_error"),
                        "failure": fs.get("failure") or {
                            "code": "VIDEO_GENERATION_FAILED",
                            "message": "Video generation failed",
                            "retryable": True,
                        },
                    }
                )
                continue
            kept.append(r)
        if filtered:
            await stream.observation(
                f"已跳过 {len(filtered)} 个生成失败的资源 "
                f"（{', '.join(f['type'] for f in filtered)}）"
                f"—— 将在聊天流中提示用户重试或调整主题",
                source="resource_capability",
                stage="quality_review",
                metadata={"filtered_failed": filtered},
            )
            logger.warning(
                f"resource_capability: pre-filtered {len(filtered)} "
                f"failed-generation resources before review "
                f"({before} -> {len(kept)}): "
                f"{[f['title'] for f in filtered]}"
            )
        return kept, filtered

    async def _review_all(
        self,
        resources: list[Resource],
        context: UnifiedContext,
        stream: StreamBus,
    ) -> list[ResourceReview]:
        """Run the quality reviewer on each resource, in parallel."""
        if not resources:
            return []

        async def _review_one(r: Resource) -> ResourceReview | None:
            try:
                return await self.quality_reviewer.process(context, resource=r, stream=stream)
            except Exception:  # noqa: BLE001
                log_degraded(
                    code="RESOURCE_QUALITY_REVIEW_FAILED",
                    source="resource_capability",
                    stage="quality_review",
                )
                return None

        tasks = [asyncio.create_task(_review_one(r)) for r in resources]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return [r for r in results if r is not None]

    async def _safety_check_all(
        self,
        resources: list[Resource],
        context: UnifiedContext,
        intent: Intent,
        stream: StreamBus,
    ) -> list[Any]:
        """Run anti-hallucination on each resource, in parallel.

        Returns a list of :class:`AntiHallucinationReport` (one per resource).
        """
        if not resources:
            return []
        from tutor.agents.safety.anti_hallucination import (
            AntiHallucinationReport,
        )

        async def _check_one(r: Resource):
            try:
                return await self.anti_hallucination.process(
                    context,
                    stream=stream,
                    resource_content=r.content,
                    topic=r.topic or intent.topic,
                )
            except Exception:  # noqa: BLE001
                log_degraded(
                    code="RESOURCE_SAFETY_CHECK_FAILED",
                    source="resource_capability",
                    stage="safety_check",
                )
                return AntiHallucinationReport(
                    overall_verdict=OverallVerdict.UNVERIFIED,
                    overall_confidence=0.5,
                    notes="Safety check failed",
                )

        tasks = [asyncio.create_task(_check_one(r)) for r in resources]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return list(results)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _is_failed_resource(r: Resource) -> bool:
    """Return True if a resource is a known-failed artifact and should
    be filtered out **before** quality review.

    Resources with a self-reported hard failure (video
    ``render_status == "failed"`` or a structured
    ``format_specific.failure``) are dropped here
    so the quality reviewer doesn't waste cycles judging a "video
    generation failed" diagnostic card. The reviewer can still
    reject other resources, but those represent LLM-judged issues,
    not deterministic "the agent already gave up" cases.

    Code resources with ``execution_status="failed"`` are NOT
    filtered here — the LLM-generated code may still be educational
    even if the local interpreter lacked the runtime dep. The
    reviewer decides.
    """
    fs = r.format_specific or {}
    if r.type == ResourceType.CODE:
        return False
    return (
        r.type == ResourceType.VIDEO and fs.get("render_status") == "failed"
    ) or isinstance(fs.get("failure"), dict)


__all__ = ["ResourceGenerationCapability"]
