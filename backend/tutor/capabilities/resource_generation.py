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
from tutor.runtime.workflow_graph import WorkflowGraph, WorkflowNode
from tutor.services.config.settings import Settings
from tutor.services.jobs.contracts import (
    ResourceArtifactNodeInput,
    ResourceArtifactNodeOutput,
    ResourceIntentNodeInput,
    ResourceIntentNodeOutput,
    ResourcePackageNodeInput,
    ResourcePedagogyNodeInput,
    ResourcePedagogyNodeOutput,
    ResourceProfileNodeInput,
    ResourceProfileNodeOutput,
    ResourceQualityNodeInput,
    ResourceQualityNodeOutput,
    ResourceSafetyNodeInput,
    ResourceSafetyNodeOutput,
    ResourceSourceNodeInput,
    ResourceSourceNodeOutput,
)
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
    public_package_dump,
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
        settings: Settings | None = None,
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
        self.settings = settings
        self.code_sandbox = code_sandbox or CodeSandboxAgent(settings=settings)
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

    async def run(
        self,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> CapabilityResult:
        """Execute the explicit resource DAG and hand its result to JobRunner."""

        graph = self.build_resource_graph(context, stream)
        execution = await graph.execute({})
        for node_name, outcome in execution.outcomes.items():
            if outcome.status not in {"failed", "skipped"}:
                continue
            await report_degraded(
                stream,
                code=outcome.error_code or "RESOURCE_WORKFLOW_NODE_FAILED",
                summary=f"资源工作流节点 {node_name} 未完成，已隔离该分支",
                source="resource_capability",
                stage=node_name,
            )

        package_outcome = execution.outcomes["package"]
        if package_outcome.status not in {"succeeded", "degraded"}:
            raise RuntimeError("RESOURCE_WORKFLOW_FAILED")
        result = graph.typed_output(execution, "package")
        if not isinstance(result, CapabilityResult):
            raise RuntimeError("RESOURCE_WORKFLOW_FAILED")
        return result

    def build_resource_graph(
        self,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> WorkflowGraph:
        """Build the validated, fixed resource-generation dependency graph."""

        async def intent_node(
            _inputs: ResourceIntentNodeInput,
        ) -> ResourceIntentNodeOutput:
            async with stream.stage(
                "intent_understanding",
                source="resource_capability",
            ):
                await stream.thinking(
                    "解析用户意图...",
                    source="resource_capability",
                )
                intent = await self.intent_agent.process(context, stream=stream)
            return self._intent_contract(intent)

        async def intent_degrade(
            _inputs: ResourceIntentNodeInput,
            _error_code: str,
        ) -> ResourceIntentNodeOutput:
            await report_degraded(
                stream,
                code="RESOURCE_INTENT_FAILED",
                summary="意图解析失败，已使用本地规则回退",
                source="resource_capability",
                stage="intent_understanding",
            )
            return self._intent_contract(parse_intent_keyword(context.user_message))

        async def profile_node(
            inputs: ResourceProfileNodeInput,
        ) -> ResourceProfileNodeOutput:
            intent = inputs.intent
            async with stream.stage("profile_loading", source="resource_capability"):
                await stream.thinking("加载学习者画像...", source="resource_capability")
                profile = await self._builder.get(context.user_id)
                snapshot = profile.to_summary()
                context.metadata["learner_profile"] = profile
            return ResourceProfileNodeOutput(
                intent=intent,
                profile_snapshot=snapshot,
            )

        async def profile_degrade(
            inputs: ResourceProfileNodeInput,
            _error_code: str,
        ) -> ResourceProfileNodeOutput:
            await report_degraded(
                stream,
                code="RESOURCE_PROFILE_LOAD_FAILED",
                summary="画像加载失败，将使用默认生成策略",
                source="resource_capability",
                stage="profile_loading",
            )
            return ResourceProfileNodeOutput(
                intent=inputs.intent,
                profile_snapshot={},
            )

        async def source_node(
            inputs: ResourceSourceNodeInput,
        ) -> ResourceSourceNodeOutput:
            profile_output = inputs.profile_snapshot
            intent = self._intent_from_contract(profile_output.intent)
            kg_summary = await self._load_kg_summary(context, stream)
            planned_types = self._plan_resources(
                intent=intent,
                profile_snapshot=dict(profile_output.profile_snapshot),
                kg_summary=kg_summary,
                metadata=dict(context.metadata or {}),
            )
            async with stream.stage("resource_planning", source="resource_capability"):
                await stream.observation(
                    f"计划生成 {len(planned_types)} 类资源："
                    f"{', '.join(item.value for item in planned_types)}",
                    source="resource_capability",
                    stage="resource_planning",
                    metadata={"types": [item.value for item in planned_types]},
                )

            source_resource: Resource | None = None
            async with stream.stage(
                "content_and_pedagogy",
                source="resource_capability",
            ):
                if ResourceType.DOCUMENT in planned_types:
                    source_resource = await self.content_expert.process(
                        context,
                        stream=stream,
                        topic=intent.topic,
                        profile=dict(profile_output.profile_snapshot),
                    )
                else:
                    await stream.observation(
                        "未计划 document 类型，跳过内容生成",
                        source="resource_capability",
                    )
            return ResourceSourceNodeOutput(
                profile=profile_output,
                kg_summary=kg_summary,
                planned_types=tuple(item.value for item in planned_types),
                source_resource=source_resource,
            )

        async def source_degrade(
            inputs: ResourceSourceNodeInput,
            _error_code: str,
        ) -> ResourceSourceNodeOutput:
            profile_output = inputs.profile_snapshot
            intent = self._intent_from_contract(profile_output.intent)
            planned_types = self._plan_resources(
                intent=intent,
                profile_snapshot=dict(profile_output.profile_snapshot),
                kg_summary={},
                metadata=dict(context.metadata or {}),
            )
            await report_degraded(
                stream,
                code="RESOURCE_CONTENT_GENERATION_FAILED",
                summary="内容生成失败",
                source="resource_capability",
                stage="content_and_pedagogy",
            )
            return ResourceSourceNodeOutput(
                profile=profile_output,
                kg_summary={},
                planned_types=tuple(item.value for item in planned_types),
                source_resource=None,
            )

        async def pedagogy_node(
            inputs: ResourcePedagogyNodeInput,
        ) -> ResourcePedagogyNodeOutput:
            source_output = inputs.source
            source_resource = source_output.source_resource
            if source_resource is None:
                return ResourcePedagogyNodeOutput(source=source_output)
            pedagogy_resource = await self.pedagogy.process(
                context,
                stream=stream,
                source_resource=source_resource,
                profile=dict(source_output.profile.profile_snapshot),
            )
            pedagogy_resource.confidence_score = max(
                pedagogy_resource.confidence_score,
                source_resource.confidence_score,
            )
            await self._emit_resource(
                pedagogy_resource,
                stream,
                "content_and_pedagogy",
            )
            return ResourcePedagogyNodeOutput(
                source=source_output,
                pedagogy_resource=pedagogy_resource,
            )

        async def pedagogy_degrade(
            inputs: ResourcePedagogyNodeInput,
            _error_code: str,
        ) -> ResourcePedagogyNodeOutput:
            source_output = inputs.source
            await report_degraded(
                stream,
                code="RESOURCE_PEDAGOGY_FAILED",
                summary="教学重构失败，已使用原始内容",
                source="resource_capability",
                stage="content_and_pedagogy",
            )
            return ResourcePedagogyNodeOutput(
                source=source_output,
                pedagogy_resource=source_output.source_resource,
            )

        async def branch_node(
            inputs: ResourceArtifactNodeInput,
            branch_name: str,
        ) -> ResourceArtifactNodeOutput:
            return await self._run_resource_branch(
                branch_name,
                inputs.pedagogy,
                context,
                stream,
            )

        async def quality_node(
            inputs: ResourceQualityNodeInput,
        ) -> ResourceQualityNodeOutput:
            return await self._run_quality_node(inputs, context, stream)

        async def quality_degrade(
            inputs: ResourceQualityNodeInput,
            _error_code: str,
        ) -> ResourceQualityNodeOutput:
            return await self._run_quality_node(inputs, context, stream)

        async def safety_node(
            inputs: ResourceSafetyNodeInput,
        ) -> ResourceSafetyNodeOutput:
            return await self._run_safety_node(
                inputs.quality,
                context,
                stream,
            )

        async def package_node(inputs: ResourcePackageNodeInput) -> CapabilityResult:
            return await self._run_package_node(
                inputs.safety,
                context,
                stream,
            )

        return WorkflowGraph(
            [
                WorkflowNode(
                    "intent",
                    (),
                    120.0,
                    intent_node,
                    intent_degrade,
                    input_model=ResourceIntentNodeInput,
                    output_model=ResourceIntentNodeOutput,
                ),
                WorkflowNode(
                    "profile_snapshot",
                    ("intent",),
                    60.0,
                    profile_node,
                    profile_degrade,
                    input_model=ResourceProfileNodeInput,
                    output_model=ResourceProfileNodeOutput,
                ),
                WorkflowNode(
                    "source",
                    ("profile_snapshot",),
                    300.0,
                    source_node,
                    source_degrade,
                    input_model=ResourceSourceNodeInput,
                    output_model=ResourceSourceNodeOutput,
                ),
                WorkflowNode(
                    "pedagogy",
                    ("source",),
                    300.0,
                    pedagogy_node,
                    pedagogy_degrade,
                    input_model=ResourcePedagogyNodeInput,
                    output_model=ResourcePedagogyNodeOutput,
                ),
                *[
                    WorkflowNode(
                        name,
                        ("pedagogy",),
                        300.0,
                        lambda inputs, branch=name: branch_node(inputs, branch),
                        input_model=ResourceArtifactNodeInput,
                        output_model=ResourceArtifactNodeOutput,
                    )
                    for name in (
                        "mindmap",
                        "exercise",
                        "code",
                        "video-code",
                        "reading",
                    )
                ],
                WorkflowNode(
                    "quality",
                    ("mindmap", "exercise", "code", "video-code", "reading"),
                    300.0,
                    quality_node,
                    quality_degrade,
                    input_model=ResourceQualityNodeInput,
                    output_model=ResourceQualityNodeOutput,
                    degrade_input_model=ResourceQualityNodeInput,
                ),
                WorkflowNode(
                    "safety",
                    ("quality",),
                    300.0,
                    safety_node,
                    input_model=ResourceSafetyNodeInput,
                    output_model=ResourceSafetyNodeOutput,
                ),
                WorkflowNode(
                    "package",
                    ("safety",),
                    120.0,
                    package_node,
                    input_model=ResourcePackageNodeInput,
                    output_model=CapabilityResult,
                ),
            ]
        )

    @staticmethod
    def _intent_contract(intent: Intent) -> ResourceIntentNodeOutput:
        return ResourceIntentNodeOutput(
            topic=intent.topic,
            scope=intent.scope,
            resource_types=tuple(item.value for item in intent.resource_types),
            prerequisites=tuple(intent.prerequisites),
            goal=intent.goal,
            raw_message=intent.raw_message,
            confidence=intent.confidence,
        )

    @staticmethod
    def _intent_from_contract(output: ResourceIntentNodeOutput) -> Intent:
        return Intent(
            topic=output.topic,
            scope=output.scope,
            resource_types=[ResourceType(item) for item in output.resource_types],
            prerequisites=list(output.prerequisites),
            goal=output.goal,
            raw_message=output.raw_message,
            confidence=output.confidence,
        )

    async def _load_kg_summary(
        self,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> dict[str, Any]:
        async with stream.stage("knowledge_graph_query", source="resource_capability"):
            try:
                service = get_knowledge_graph_service()
                course = context.metadata.get("course") or service.default_course()
                if not course or not service.has_course(course):
                    return {}
                from tutor.services.learner_profile.schema import LearnerProfile

                profile = context.metadata.get("learner_profile")
                if not isinstance(profile, LearnerProfile):
                    profile = LearnerProfile()
                located = service.locate(course, profile)
                summary = {
                    "course": course,
                    "mastered_count": len(located["mastered"]),
                    "unmastered_count": len(located["unmastered"]),
                    "next_targets": located["next_targets"][:5],
                }
                await stream.observation(
                    f"知识图谱定位：掌握 {summary['mastered_count']}，未掌握 {summary['unmastered_count']}",
                    source="resource_capability",
                    stage="knowledge_graph_query",
                    metadata=summary,
                )
                return summary
            except Exception:  # noqa: BLE001
                await report_degraded(
                    stream,
                    code="RESOURCE_KNOWLEDGE_GRAPH_FAILED",
                    summary="知识图谱查询失败，已跳过图谱增强",
                    source="resource_capability",
                    stage="knowledge_graph_query",
                )
                return {}

    async def _run_resource_branch(
        self,
        branch_name: str,
        pedagogy_output: ResourcePedagogyNodeOutput,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> ResourceArtifactNodeOutput:
        planned = set(pedagogy_output.source.planned_types)
        intent = self._intent_from_contract(pedagogy_output.source.profile.intent)
        profile_snapshot = dict(pedagogy_output.source.profile.profile_snapshot)
        base_resource = pedagogy_output.pedagogy_resource or pedagogy_output.source.source_resource
        source_content = base_resource.content if base_resource is not None else ""
        resources: list[Resource] = []

        if branch_name == "mindmap":
            async with stream.stage(
                "parallel_resource_generation",
                source="resource_capability",
            ):
                artifact_calls: list[tuple[ResourceType, Any]] = []
                if ResourceType.MINDMAP.value in planned:
                    artifact_calls.append(
                        (
                            ResourceType.MINDMAP,
                            self.multimedia.process(
                                context,
                                stream=stream,
                                topic=intent.topic,
                                source_content=source_content,
                                profile=profile_snapshot,
                            ),
                        )
                    )
                if ResourceType.PPT.value in planned:
                    artifact_calls.append(
                        (
                            ResourceType.PPT,
                            self.ppt_generator.process(
                                topic=intent.topic,
                                source_content=source_content,
                                profile=profile_snapshot,
                                package_id=None,
                                stream=stream,
                            ),
                        )
                    )
                results = await asyncio.gather(
                    *(call for _, call in artifact_calls),
                    return_exceptions=True,
                )
                for (resource_type, _), result in zip(
                    artifact_calls,
                    results,
                    strict=True,
                ):
                    if isinstance(result, Exception):
                        log_degraded(
                            code=(
                                f"RESOURCE_{resource_type.value.upper()}_"
                                "GENERATION_FAILED"
                            ),
                            source="resource_capability",
                            stage="parallel_resource_generation",
                        )
                        continue
                    resources.append(result)
        elif branch_name == "exercise" and ResourceType.EXERCISE.value in planned:
            resources.append(
                await self.exercise_generator.process(
                    context,
                    stream=stream,
                    topic=intent.topic,
                    source_content=source_content,
                    profile=profile_snapshot,
                )
            )
        elif branch_name == "code" and ResourceType.CODE.value in planned:
            resources.append(
                await self.code_sandbox.process(
                    context,
                    stream=stream,
                    topic=intent.topic,
                    source_content=source_content,
                    profile=profile_snapshot,
                    run_locally=True,
                )
            )
        elif branch_name == "video-code" and ResourceType.VIDEO.value in planned:
            resources.append(
                await self.manim_video.process(
                    context,
                    stream=stream,
                    topic=intent.topic,
                    source_content=source_content,
                    profile=profile_snapshot,
                )
            )
        elif branch_name == "reading" and ResourceType.READING.value in planned:
            resources.append(
                await self._generate_reading(
                    topic=intent.topic,
                    profile_snapshot=profile_snapshot,
                    source_content=source_content,
                    stream=stream,
                )
            )

        for resource in resources:
            await self._emit_resource(
                resource,
                stream,
                "parallel_resource_generation",
            )
        return ResourceArtifactNodeOutput(
            pedagogy=pedagogy_output,
            resources=tuple(resources),
        )

    async def _emit_resource(
        self,
        resource: Resource,
        stream: StreamBus,
        stage: str,
    ) -> None:
        try:
            self._canonicalize_resource_artifacts(resource)
            await stream.resource(
                resource,
                source="resource_capability",
                stage=stage,
            )
        except Exception:  # noqa: BLE001
            log_degraded(
                code="RESOURCE_STREAM_EMIT_FAILED",
                source="resource_capability",
                stage=stage,
            )

    async def _run_quality_node(
        self,
        inputs: ResourceQualityNodeInput,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> ResourceQualityNodeOutput:
        branch_outputs = list(inputs.available_outputs())
        if not branch_outputs:
            raise RuntimeError("RESOURCE_QUALITY_INPUT_MISSING")
        pedagogy_output = branch_outputs[0].pedagogy
        candidates: list[Resource] = []
        if pedagogy_output.pedagogy_resource is not None:
            candidates.append(pedagogy_output.pedagogy_resource)
        seen_ids = {resource.resource_id for resource in candidates}
        for output in branch_outputs:
            for resource in output.resources:
                if resource.resource_id in seen_ids:
                    continue
                seen_ids.add(resource.resource_id)
                candidates.append(resource)

        candidates, filtered_failed = await self._prefilter_failed_resources(
            candidates,
            stream,
        )
        malformed: list[dict[str, Any]] = []
        valid_candidates: list[Resource] = []
        for resource in candidates:
            if _is_malformed_resource(resource):
                malformed.append(
                    {
                        "resource_id": resource.resource_id,
                        "type": resource.type.value,
                        "reason": "malformed_resource",
                    }
                )
                continue
            valid_candidates.append(resource)
        filtered_failed.extend(malformed)

        async with stream.stage("quality_review", source="resource_capability"):
            reviews = await self._review_all(valid_candidates, context, stream)
        review_by_id = {review.resource_id: review for review in reviews}
        approved: list[Resource] = []
        approved_reviews: list[ResourceReview] = []
        filtered_reviews: list[dict[str, Any]] = []
        for resource in valid_candidates:
            review = review_by_id.get(resource.resource_id)
            if review is None:
                filtered_reviews.append(
                    {
                        "resource_id": resource.resource_id,
                        "reason": "quality_review_failed",
                    }
                )
                continue
            if review.verdict == ReviewVerdict.REJECT:
                filtered_reviews.append(
                    {
                        "resource_id": resource.resource_id,
                        "reason": "quality_review_rejected",
                    }
                )
                continue
            resource.metadata["review"] = {
                "verdict": review.verdict.value,
                "quality_score": review.quality_score,
                "issues": review.issues,
                "suggestions": review.suggestions,
            }
            approved.append(resource)
            approved_reviews.append(review)

        if filtered_reviews:
            await stream.observation(
                f"已过滤 {len(filtered_reviews)} 个未通过质量审核的资源",
                source="resource_capability",
                stage="quality_review",
                metadata={"filtered_count": len(filtered_reviews)},
            )
        return ResourceQualityNodeOutput(
            pedagogy=pedagogy_output,
            resources=tuple(approved),
            reviews=tuple(approved_reviews),
            filtered_failed=tuple(filtered_failed),
            filtered_reviews=tuple(filtered_reviews),
        )

    async def _run_safety_node(
        self,
        quality_output: ResourceQualityNodeOutput,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> ResourceSafetyNodeOutput:
        resources = list(quality_output.resources)
        intent = self._intent_from_contract(quality_output.pedagogy.source.profile.intent)
        async with stream.stage("anti_hallucination", source="resource_capability"):
            reports = await self._safety_check_all(
                resources,
                context,
                intent,
                stream,
            )
        kept: list[Resource] = []
        kept_reports: list[Any] = []
        filtered: list[dict[str, Any]] = []
        for resource, report in zip(resources, reports, strict=True):
            resource.metadata["safety"] = report.to_dict()
            if report.overall_verdict == OverallVerdict.UNSAFE:
                filtered.append(
                    {
                        "resource_id": resource.resource_id,
                        "reason": "anti_hallucination_unsafe",
                    }
                )
                continue
            kept.append(resource)
            kept_reports.append(report)
        if filtered:
            await stream.observation(
                f"已过滤 {len(filtered)} 个安全校验未通过的资源",
                source="resource_capability",
                stage="anti_hallucination",
                metadata={
                    "unsafe_count": len(filtered),
                    "kept_count": len(kept),
                },
            )
        return ResourceSafetyNodeOutput(
            quality=quality_output,
            resources=tuple(kept),
            safety_reports=tuple(kept_reports),
            filtered_safety=tuple(filtered),
        )

    async def _run_package_node(
        self,
        safety_output: ResourceSafetyNodeOutput,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> CapabilityResult:
        quality = safety_output.quality
        source = quality.pedagogy.source
        intent = self._intent_from_contract(source.profile.intent)
        package = ResourcePackage(
            topic=intent.topic,
            resources=list(safety_output.resources),
            target_profile_snapshot=dict(source.profile.profile_snapshot),
            learning_path_summary=dict(source.kg_summary),
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
                "review_count": len(quality.reviews),
                "passing_reviews": sum(review.verdict == ReviewVerdict.PASS for review in quality.reviews),
                "safety_blocked": len(safety_output.filtered_safety),
                "filtered_failed": list(quality.filtered_failed),
                "filtered_reviews": list(quality.filtered_reviews),
                "filtered_safety": list(safety_output.filtered_safety),
            },
        )
        package.associate_originating_job(context.job_id)
        session_id = context.session_id or ""
        if session_id:
            package.metadata["session_id"] = session_id
        for resource in package.resources:
            resource.metadata.setdefault("package_id", package.package_id)
            if session_id:
                resource.metadata.setdefault("session_id", session_id)
            self._canonicalize_resource_artifacts(resource)

        async with stream.stage("path_integration", source="resource_capability"):
            try:
                profile = await self._builder.get(context.user_id)
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
                await self._builder.store.replace(
                    profile,
                    source="resource_capability",
                )
            except Exception:  # noqa: BLE001
                log_degraded(
                    code="RESOURCE_PROFILE_METADATA_FAILED",
                    source="resource_capability",
                    stage="path_integration",
                )

        try:
            self._relocate_ppt_artifacts(package)
        except Exception:  # noqa: BLE001
            log_degraded(
                code="RESOURCE_PPT_RELOCATION_FAILED",
                source="resource_capability",
                stage="persistence",
            )
        async with stream.stage("persistence", source="resource_capability"):
            try:
                for resource in package.resources:
                    resource.metadata["package_persisted"] = True
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
                for resource in package.resources:
                    resource.metadata["package_persisted"] = False
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
            format_specific = resource.format_specific or {}
            artifact_key = format_specific.get("artifact_key")
            if artifact_key:
                artifact_refs.append(
                    ArtifactRef(
                        name=PurePosixPath(str(artifact_key)).name,
                        kind=resource.type.value,
                        artifact_key=str(artifact_key),
                    )
                )
            for raw in format_specific.get("artifacts") or []:
                try:
                    reference = ArtifactRef.model_validate(raw)
                except Exception:  # noqa: BLE001
                    continue
                if reference.artifact_key:
                    artifact_refs.append(reference)

        surviving_ids = {resource.resource_id for resource in package.resources}
        reviews = [review for review in quality.reviews if review.resource_id in surviving_ids]
        return CapabilityResult(
            assistant_message=f"已生成 {len(package.resources)} 项学习资源",
            payload={
                "package": public_package_dump(package),
                "summary": package.summary(),
                "reviews": [
                    {
                        "resource_id": review.resource_id,
                        "verdict": review.verdict.value,
                        "quality_score": review.quality_score,
                    }
                    for review in reviews
                ],
                "kg_summary": dict(source.kg_summary),
                "next_step": "open_resource_cards",
            },
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
        *,
        persist_package: bool = True,
        emit_resource: bool = True,
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
                    code=code,
                    scene_class=scene,
                    job_id=context.job_id or None,
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
                    failure = render_result.failure
                    if failure is not None:
                        res.format_specific["render_failure"] = failure.to_dict()
                        res.format_specific["render_error_code"] = failure.error_code
                        res.format_specific["render_error"] = failure.summary
                        if failure.log_artifact_key:
                            log_name = failure.log_artifact_key.rsplit("/", 1)[-1]
                            artifacts = [
                                item
                                for item in (res.format_specific.get("artifacts") or [])
                                if not (
                                    isinstance(item, dict)
                                    and item.get("kind") == "render_log"
                                )
                            ]
                            artifacts.append(
                                {
                                    "name": log_name,
                                    "kind": "render_log",
                                    "artifact_key": failure.log_artifact_key,
                                }
                            )
                            res.format_specific["artifacts"] = artifacts
                    else:
                        res.format_specific["render_error_code"] = "internal_error"
                        res.format_specific["render_error"] = "Video rendering failed"
                else:
                    for key in (
                        "render_failure",
                        "render_error_code",
                        "render_error",
                    ):
                        res.format_specific.pop(key, None)
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
            res.format_specific["render_error_code"] = "internal_error"
            res.format_specific["render_error"] = "Video rendering failed"
            res.format_specific["render_failure"] = {
                "error_code": "internal_error",
                "summary": "Video rendering failed internally",
                "traceback_tail": [],
                "log_artifact_key": "",
            }
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
            if emit_resource:
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
            if persist_package:
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
                f"({before} -> {len(kept)})"
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


def _is_malformed_resource(resource: Resource) -> bool:
    """Return whether a generated resource lacks its minimum usable payload."""

    if not resource.title.strip() or not resource.content.strip():
        return True
    format_specific = resource.format_specific or {}
    if resource.type == ResourceType.VIDEO:
        status = format_specific.get("render_status")
        return status in {"pending", "ready"} and (
            not str(format_specific.get("manim_code") or "").strip()
            or not str(format_specific.get("scene_class") or "").strip()
        )
    if resource.type == ResourceType.CODE:
        return not str(format_specific.get("code") or "").strip()
    if resource.type == ResourceType.MINDMAP:
        return not str(format_specific.get("mermaid_dsl") or "").strip()
    if resource.type == ResourceType.EXERCISE:
        return not bool(format_specific.get("questions"))
    return False


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
