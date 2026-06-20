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
    9. result_emission         → RESULT event + DONE

Each Agent emits its own stage events; the capability emits high-level
stage_start / stage_end wrappers around each pipeline stage.

Errors are contained per-stage: a failure in one branch doesn't kill
the whole generation. The package will simply have one fewer resource.
"""

from __future__ import annotations

import asyncio
import traceback
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
from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
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

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
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
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"IntentUnderstandingAgent failed: {exc!r}")
                await stream.error(
                    f"意图解析失败 (回退): {exc}", source="resource_capability"
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
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"Profile load failed: {exc!r}")
                await stream.error(
                    f"画像加载失败: {exc}", source="resource_capability"
                )
                profile_snapshot = {}

        if intent is None:
            intent = parse_intent_keyword(context.user_message)

        # ------------------------------------------------------------------
        # Stage 3: Knowledge graph query
        # ------------------------------------------------------------------
        kg_summary: dict[str, Any] = {}
        kg_recommendations: list[Any] = []
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
                    kg_recommendations = svc.recommend_next(
                        course, prof_obj, limit=5
                    )
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
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"KG query failed: {exc!r}")
                await stream.error(
                    f"知识图谱查询失败 (回退): {exc}",
                    source="resource_capability",
                )

        # ------------------------------------------------------------------
        # Stage 4: Resource planning
        # ------------------------------------------------------------------
        async with stream.stage("resource_planning", source="resource_capability"):
            planned_types = self._plan_resources(
                intent=intent,
                profile_snapshot=profile_snapshot,
                kg_summary=kg_summary,
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
                except Exception as exc:  # noqa: BLE001
                    logger.exception(f"Content/Pedagogy failed: {exc!r}")
                    await stream.error(
                        f"内容生成失败: {exc}", source="resource_capability"
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
        # Attach review + safety to each resource
        review_by_id = {r.resource_id: r for r in reviews}
        safety_by_id = {s.fact_check.topic if False else i: s for i, s in enumerate(safety_reports)}
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
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Profile metadata update failed: {exc!r}")

        # ------------------------------------------------------------------
        # Stage 11: Persistence — write the package to the persistent store
        # ------------------------------------------------------------------
        # First, move any PPT artifacts generated with a placeholder
        # package_id to the real one. This is a small bookkeeping step
        # so the file layout mirrors the resource_packages DB layout.
        try:
            self._relocate_ppt_artifacts(package)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"PPT artifact relocation failed: {exc!r}")

        async with stream.stage("persistence", source="resource_capability"):
            try:
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
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"ResourcePackage persistence failed: {exc!r}")
                await stream.error(
                    f"资源包持久化失败 (不影响本轮): {exc}",
                    source="resource_capability",
                )

        # ------------------------------------------------------------------
        # Emit final result
        # ------------------------------------------------------------------
        await stream.result(
            {
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
            },
            source="resource_capability",
        )
        await stream.done(source="resource_capability")

    # ------------------------------------------------------------------
    # PPT bookkeeping
    # ------------------------------------------------------------------

    def _relocate_ppt_artifacts(self, package: ResourcePackage) -> None:
        """Move any PPT files written under ``ad_hoc/`` to
        ``<data_dir>/ppt/<package_id>/`` and update the resource's
        ``format_specific["pptx_path"]`` in place.
        """
        from pathlib import Path

        from tutor.services.config.settings import get_settings
        from tutor.services.ppt import get_ppt_service

        ppt_root = get_ppt_service().output_dir
        for r in package.resources:
            if r.type != ResourceType.PPT:
                continue
            pptx_path = (r.format_specific or {}).get("pptx_path")
            if not pptx_path:
                continue
            src = Path(pptx_path)
            if not src.exists():
                continue
            # Already under the right package dir?
            try:
                if src.parent.parent == ppt_root and src.parent.name == package.package_id:
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
            r.format_specific["pptx_path"] = str(dst)

    # ------------------------------------------------------------------
    # Resource planning
    # ------------------------------------------------------------------

    def _plan_resources(
        self,
        *,
        intent: Intent,
        profile_snapshot: dict[str, Any],
        kg_summary: dict[str, Any],
    ) -> list[ResourceType]:
        """Decide which resource types to generate.

        Strategy:
        1. Start from intent.resource_types
        2. If modality preferences are set, **up-rank** matching types:
           - high video → keep video
           - high code → keep code
           - high diagram → keep mindmap
           - high exercise → keep exercise
           - high text → keep document, reading
        3. Skip VIDEO if scope == "overview" (animated video is heavy)
        """
        types = list(intent.resource_types)

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
            if ResourceType.VIDEO not in types:
                types.append(ResourceType.VIDEO)
            if ResourceType.EXERCISE not in types:
                types.append(ResourceType.EXERCISE)

        # Always include document unless explicitly excluded
        if ResourceType.DOCUMENT not in types and intent.scope != "deep_dive":
            # Document is optional for deep_dive (video/reading may suffice)
            if "document" in (intent.raw_message or "").lower():
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
        intent: Intent,
        profile_snapshot: dict[str, Any],
        source_content: str,
        planned_types: list[ResourceType],
        stream: StreamBus,
    ) -> list[Resource]:
        """Run the type-specific agents in parallel."""
        tasks: list[tuple[ResourceType, asyncio.Task]] = []

        async def _safe(coro, rtype: ResourceType) -> Resource | None:
            try:
                return await coro
            except Exception as exc:  # noqa: BLE001
                logger.exception(f"{rtype.value} generation failed: {exc!r}")
                await stream.error(
                    f"{rtype.value} 生成失败: {exc}",
                    source="resource_capability",
                )
                return None

        if ResourceType.MINDMAP in planned_types:
            tasks.append((
                ResourceType.MINDMAP,
                asyncio.create_task(_safe(
                    self.multimedia.process(
                        context=None,  # type: ignore[arg-type]
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
                        context=None,  # type: ignore[arg-type]
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
                        context=None,  # type: ignore[arg-type]
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
                        context=None,  # type: ignore[arg-type]
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

        results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=False)
        return [r for r in results if r is not None]

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
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Quality review failed for {r.resource_id}: {exc!r}")
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
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"AntiHallucination failed for {r.resource_id}: {exc!r}"
                )
                return AntiHallucinationReport(
                    overall_verdict=OverallVerdict.UNVERIFIED,
                    overall_confidence=0.5,
                    notes=f"safety check failed: {exc}",
                )

        tasks = [asyncio.create_task(_check_one(r)) for r in resources]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return list(results)


__all__ = ["ResourceGenerationCapability"]


def _unused_traceback_import() -> None:
    """Reference import to keep it for debugging future use."""
    _ = traceback.format_exc
