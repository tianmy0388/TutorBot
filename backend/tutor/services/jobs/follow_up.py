"""Durable, idempotent follow-up child-job scheduling."""

from __future__ import annotations

from collections.abc import Collection, Mapping

from tutor.core.capability_protocol import BaseCapability, CapabilityManifest
from tutor.core.capability_result import CapabilityResult, FollowUpTaskSpec
from tutor.core.context import UnifiedContext
from tutor.core.stream_bus import StreamBus
from tutor.services.jobs.schema import Job
from tutor.services.jobs.store import JobStore
from tutor.services.resource_package.schema import ArtifactRef


async def _require_current_claim(context: UnifiedContext) -> None:
    validator = context.metadata.get("_claim_validator")
    if callable(validator) and not await validator():
        raise PermissionError("follow-up claim is no longer current")


class FollowUpScheduler:
    """Persist follow-up specs as child jobs before parent terminalization."""

    def __init__(self, store: JobStore) -> None:
        self.store = store

    async def enqueue(
        self,
        parent_job_id: str,
        specs: tuple[FollowUpTaskSpec, ...],
    ) -> list[Job]:
        for spec in specs:
            validate_follow_up_spec(spec)
        children: list[Job] = []
        for spec in specs:
            children.append(
                await self.store.create_child_if_absent(
                    parent_job_id=parent_job_id,
                    task_kind=spec.kind,
                    dedupe_key=spec.dedupe_key,
                    payload=spec.payload,
                )
            )
        return children


class VideoRenderFollowUpCapability(BaseCapability):
    """Execute one persisted pending-video spec on a child stream."""

    manifest = CapabilityManifest(
        name="video_render",
        description="内部持久化 Manim 视频渲染子任务",
        stages=["video_rendering"],
        tags=["internal", "follow_up", "video"],
    )

    def __init__(self, package_store=None, settings=None) -> None:
        super().__init__()
        self._package_store = package_store
        self._settings = settings

    async def run(
        self,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> CapabilityResult:
        from tutor.capabilities.resource_generation import (
            ResourceGenerationCapability,
        )
        from tutor.services.resource_package import get_resource_package_store

        package_id = str(context.metadata.get("package_id") or "")
        resource_id = str(context.metadata.get("resource_id") or "")
        claim_validator = context.metadata.get("_claim_validator")
        claim_guard = context.metadata.get("_claim_guard")

        async def require_current_claim() -> None:
            if callable(claim_validator) and not await claim_validator():
                raise PermissionError("follow-up claim is no longer current")

        await require_current_claim()
        package_store = self._package_store or get_resource_package_store()
        package = await package_store.get_for_user(package_id, context.user_id)
        if package is None:
            raise PermissionError("Video package is unavailable for this user")
        if not await package_store.owns_resource(
            package_id,
            resource_id,
            context.user_id,
        ):
            raise PermissionError("Video resource is unavailable for this user")
        resource = next(
            (item for item in package.resources if item.resource_id == resource_id),
            None,
        )
        if resource is None:
            raise RuntimeError("Video resource is unavailable")

        capability = ResourceGenerationCapability(
            package_store=package_store,
            settings=self._settings,
        )
        await capability._render_one_video(
            resource,
            package,
            context,
            stream,
            persist_package=False,
            emit_resource=False,
        )
        await require_current_claim()

        async def persist_resource() -> None:
            await package_store.update_resource(
                package.package_id,
                resource,
                user_id=context.user_id,
            )

        if not callable(claim_guard) or not await claim_guard(persist_resource):
            raise PermissionError("follow-up claim is no longer current")
        render_status = str(
            (resource.format_specific or {}).get("render_status") or "failed"
        )
        if render_status != "ready":
            raise RuntimeError("Video rendering failed")

        artifacts: tuple[ArtifactRef, ...] = ()
        artifact_key = (resource.format_specific or {}).get("artifact_key")
        if artifact_key:
            artifacts = (
                ArtifactRef(
                    name=str(artifact_key).rsplit("/", 1)[-1],
                    kind="video",
                    artifact_key=str(artifact_key),
                ),
            )
        return CapabilityResult(
            assistant_message="视频渲染完成",
            payload={
                "package_id": package.package_id,
                "resource_id": resource.resource_id,
                "render_status": render_status,
            },
            artifacts=artifacts,
        )


class _VideoRepairError(RuntimeError):
    def __init__(self, failure) -> None:  # type: ignore[no-untyped-def]
        super().__init__(failure.summary)
        self.failure = failure


class VideoRepairFollowUpCapability(BaseCapability):
    """Regenerate a failed video's complete source, validate, then render once."""

    manifest = CapabilityManifest(
        name="video_repair_render",
        description="用户触发的持久化 Manim 全源码修复任务",
        stages=["video_repair_generation", "video_repair_rendering"],
        tags=["internal", "follow_up", "video", "repair"],
    )

    def __init__(
        self,
        package_store=None,
        settings=None,
        repair_agent=None,
        render_service=None,
        runtime_namespace=None,
        runtime_versions=None,
    ) -> None:
        super().__init__()
        self._package_store = package_store
        self._settings = settings
        self._repair_agent = repair_agent
        self._render_service = render_service
        self._runtime_namespace = runtime_namespace
        self._runtime_versions = runtime_versions

    async def run(
        self,
        context: UnifiedContext,
        stream: StreamBus,
    ) -> CapabilityResult:
        from pathlib import Path

        from tutor.agents.resource.manim_repair import ManimRepairAgent
        from tutor.services.artifacts import UnsafeArtifactKey, to_artifact_key
        from tutor.services.config.settings import get_settings
        from tutor.services.manim_render.candidate_validation import (
            validate_manim_candidate,
        )
        from tutor.services.manim_render.executor import RenderFailure
        from tutor.services.manim_render.service import (
            ManimRenderService,
            get_manim_render_service,
        )
        from tutor.services.resource_package import get_resource_package_store

        package_id = str(context.metadata.get("package_id") or "")
        resource_id = str(context.metadata.get("resource_id") or "")
        failed_revision = int(context.metadata.get("failed_revision") or 0)
        await _require_current_claim(context)
        store = self._package_store or get_resource_package_store()
        package = await store.get_for_user(package_id, context.user_id)
        if package is None or not await store.owns_resource(
            package_id, resource_id, context.user_id
        ):
            raise PermissionError("Video resource is unavailable for this user")
        resource = next(
            (item for item in package.resources if item.resource_id == resource_id),
            None,
        )
        if resource is None:
            raise RuntimeError("Video resource is unavailable")
        payload = resource.format_specific or {}
        if (
            int(payload.get("source_revision") or 0) != failed_revision
            or payload.get("repair_job_id") != context.job_id
        ):
            raise PermissionError("Video repair is no longer current")

        async def mark_running() -> None:
            def mutation(current_payload) -> None:  # type: ignore[no-untyped-def]
                current_payload["repair_status"] = "running"

            updated = await store.mutate_video_repair_if_current(
                package_id=package_id,
                resource_id=resource_id,
                user_id=context.user_id,
                expected_source_revision=failed_revision,
                expected_repair_job_id=context.job_id,
                mutation=mutation,
            )
            if updated is None:
                raise PermissionError("Video repair is no longer current")

        await self._guarded_commit(context, mark_running)
        original_failure = _render_failure_from_payload(payload, RenderFailure)
        runtime_versions, runtime_namespace = self._runtime()
        workdir = Path(
            getattr(
                getattr(self._render_service, "executor", None),
                "temp_dir",
                get_settings().manim_temp_dir,
            )
        )
        workdir.mkdir(parents=True, exist_ok=True)
        agent = self._repair_agent or ManimRepairAgent()

        try:
            candidate = await agent.regenerate(
                context,
                failed_code=str(payload.get("manim_code") or ""),
                failure=original_failure,
                runtime=runtime_versions,
            )
            validation = validate_manim_candidate(
                candidate,
                workdir=workdir,
                runtime_namespace=runtime_namespace,
            )
            if not validation.valid:
                issue_text = "\n".join(
                    f"{issue.code}: {issue.message}" for issue in validation.issues
                )
                log_key = ManimRenderService._write_log_artifact(
                    context.job_id,
                    attempt_label="candidate-validation-01",
                    stdout="",
                    stderr=issue_text,
                )
                validation_failure = RenderFailure(
                    error_code="candidate_validation_failed",
                    summary="Regenerated Manim source failed deterministic validation",
                    traceback_tail=tuple(issue_text.splitlines()[-40:]),
                    log_artifact_key=log_key,
                )
                candidate = await agent.regenerate(
                    context,
                    failed_code=candidate,
                    failure=validation_failure,
                    runtime=runtime_versions,
                )
                validation = validate_manim_candidate(
                    candidate,
                    workdir=workdir,
                    runtime_namespace=runtime_namespace,
                )
                if not validation.valid:
                    issue_text = "\n".join(
                        f"{issue.code}: {issue.message}" for issue in validation.issues
                    )
                    log_key = ManimRenderService._write_log_artifact(
                        context.job_id,
                        attempt_label="candidate-validation-02",
                        stdout="",
                        stderr=issue_text,
                    )
                    raise _VideoRepairError(
                        RenderFailure(
                            error_code="candidate_validation_failed",
                            summary="Regenerated Manim source failed deterministic validation",
                            traceback_tail=tuple(issue_text.splitlines()[-40:]),
                            log_artifact_key=log_key,
                        )
                    )

            renderer = self._render_service or get_manim_render_service()
            render_result = await renderer.render(
                code=candidate,
                scene_class="MainScene",
                job_id=context.job_id,
            )
            if not render_result.success:
                render_failure = getattr(render_result, "failure", None)
                if render_failure is None:
                    message = "Video repair render failed internally"
                    log_key = ManimRenderService._write_log_artifact(
                        context.job_id,
                        attempt_label="repair-render-internal-error",
                        stdout="",
                        stderr=message,
                        operator_stderr=str(getattr(render_result, "error", "") or message),
                    )
                    render_failure = RenderFailure(
                        error_code="repair_render_failed",
                        summary=message,
                        traceback_tail=(message,),
                        log_artifact_key=log_key,
                    )
                raise _VideoRepairError(render_failure)

            if not getattr(render_result, "video_path", None) or not getattr(
                render_result, "public_url", None
            ):
                message = "Video repair produced no publishable video"
                log_key = ManimRenderService._write_log_artifact(
                    context.job_id,
                    attempt_label="repair-publish-missing",
                    stdout="",
                    stderr=message,
                )
                raise _VideoRepairError(
                    RenderFailure(
                        error_code="repair_publish_failed",
                        summary=message,
                        traceback_tail=(message,),
                        log_artifact_key=log_key,
                    )
                )

            artifact_key = ""
            video_path = getattr(render_result, "video_path", None)
            if video_path:
                try:
                    artifact_key = to_artifact_key(
                        Path(video_path), get_settings().data_dir
                    )
                except UnsafeArtifactKey:
                    artifact_key = ""

            async def persist_success() -> None:
                def mutation(current_payload) -> None:  # type: ignore[no-untyped-def]
                    current_payload.update(
                        {
                            "manim_code": candidate,
                            "scene_class": "MainScene",
                            "render_status": "ready",
                            "repair_status": "ready",
                            "source_revision": failed_revision + 1,
                        }
                    )
                    current_payload.pop("video_url", None)
                    current_payload.pop("artifact_key", None)
                    if getattr(render_result, "public_url", None):
                        current_payload["video_url"] = render_result.public_url
                    if artifact_key:
                        current_payload["artifact_key"] = artifact_key
                    if getattr(render_result, "duration_seconds", None):
                        current_payload["duration_seconds"] = render_result.duration_seconds
                    for key in (
                        "render_failure",
                        "render_error_code",
                        "render_error",
                    ):
                        current_payload.pop(key, None)
                    _append_repair_history(
                        current_payload,
                        job_id=context.job_id,
                        failed_revision=failed_revision,
                        status="ready",
                    )

                updated = await store.mutate_video_repair_if_current(
                    package_id=package_id,
                    resource_id=resource_id,
                    user_id=context.user_id,
                    expected_source_revision=failed_revision,
                    expected_repair_job_id=context.job_id,
                    mutation=mutation,
                )
                if updated is None:
                    raise PermissionError("Video repair is no longer current")

            await self._guarded_commit(context, persist_success)
        except _VideoRepairError as exc:
            await self._persist_failure(
                context, store, package_id, resource_id, failed_revision, exc.failure
            )
            raise RuntimeError("Video repair failed") from None
        except Exception:
            message = "Video repair generation failed"
            log_key = ManimRenderService._write_current_exception_log_artifact(
                context.job_id,
                attempt_label="repair-generation-error",
                public_stderr=message,
            )
            failure = RenderFailure(
                error_code="repair_generation_failed",
                summary=message,
                traceback_tail=(message,),
                log_artifact_key=log_key,
            )
            await self._persist_failure(
                context, store, package_id, resource_id, failed_revision, failure
            )
            raise RuntimeError("Video repair failed") from None

        return CapabilityResult(
            assistant_message="视频修复完成",
            payload={
                "package_id": package_id,
                "resource_id": resource_id,
                "render_status": "ready",
                "source_revision": failed_revision + 1,
            },
            artifacts=(
                ArtifactRef(
                    name=artifact_key.rsplit("/", 1)[-1],
                    kind="video",
                    artifact_key=artifact_key,
                ),
            ) if artifact_key else (),
        )

    @staticmethod
    async def _guarded_commit(context: UnifiedContext, operation) -> None:  # type: ignore[no-untyped-def]
        await _require_current_claim(context)
        guard = context.metadata.get("_claim_guard")
        if callable(guard):
            if not await guard(operation):
                raise PermissionError("follow-up claim is no longer current")
        else:
            await operation()

    async def _persist_failure(
        self,
        context,
        store,
        package_id,
        resource_id,
        failed_revision,
        failure,
    ) -> None:  # type: ignore[no-untyped-def]
        async def persist() -> None:
            safe_log_key = _safe_log_artifact_key(failure.log_artifact_key)

            def mutation(payload) -> None:  # type: ignore[no-untyped-def]
                payload["repair_status"] = "failed"
                _append_repair_history(
                    payload,
                    job_id=context.job_id,
                    failed_revision=failed_revision,
                    status="failed",
                    failure=failure,
                )
                if safe_log_key:
                    artifacts = list(payload.get("artifacts") or [])
                    if not any(
                        isinstance(item, dict)
                        and item.get("artifact_key") == safe_log_key
                        for item in artifacts
                    ):
                        artifacts.append(
                            {
                                "name": safe_log_key.rsplit("/", 1)[-1],
                                "kind": "render_log",
                                "artifact_key": safe_log_key,
                            }
                        )
                    payload["artifacts"] = artifacts[-20:]

            updated = await store.mutate_video_repair_if_current(
                package_id=package_id,
                resource_id=resource_id,
                user_id=context.user_id,
                expected_source_revision=failed_revision,
                expected_repair_job_id=context.job_id,
                mutation=mutation,
            )
            if updated is None:
                raise PermissionError("Video repair is no longer current")

        await self._guarded_commit(context, persist)

    def _runtime(
        self,
    ) -> tuple[
        dict[str, str],
        Mapping[str, object] | Collection[str],
    ]:
        if self._runtime_versions is not None and self._runtime_namespace is not None:
            namespace = (
                dict(self._runtime_namespace)
                if isinstance(self._runtime_namespace, Mapping)
                else set(self._runtime_namespace)
            )
            return dict(self._runtime_versions), namespace
        import platform
        try:
            import manim
        except Exception:
            return {"python": platform.python_version(), "manim": "unavailable"}, set()
        return (
            {
                "python": platform.python_version(),
                "manim": str(getattr(manim, "__version__", "unknown")),
            },
            dict(vars(manim)),
        )


def _render_failure_from_payload(payload, render_failure_type):  # type: ignore[no-untyped-def]
    raw = payload.get("render_failure")
    if isinstance(raw, dict):
        return render_failure_type(
            error_code=str(raw.get("error_code") or payload.get("render_error_code") or "render_failed"),
            summary=str(raw.get("summary") or payload.get("render_error") or "Video rendering failed"),
            traceback_tail=tuple(str(line) for line in (raw.get("traceback_tail") or [])[-120:]),
            log_artifact_key=str(raw.get("log_artifact_key") or ""),
        )
    return render_failure_type(
        error_code=str(payload.get("render_error_code") or "render_failed"),
        summary=str(payload.get("render_error") or "Video rendering failed"),
    )


def _append_repair_history(
    payload,
    *,
    job_id,
    failed_revision,
    status,
    failure=None,
):  # type: ignore[no-untyped-def]
    from tutor.services.manim_render.executor import sanitize_public_diagnostic

    history = [
        normalized
        for raw in list(payload.get("repair_history") or [])[-9:]
        if isinstance(raw, dict)
        for normalized in [_normalize_repair_history_record(raw)]
    ]
    record = {
        "job_id": sanitize_public_diagnostic(str(job_id))[:96],
        "failed_revision": int(failed_revision),
        "status": str(status),
    }
    if failure is not None:
        record.update(
            {
                "error_code": sanitize_public_diagnostic(
                    str(failure.error_code)
                )[:120],
                "summary": sanitize_public_diagnostic(
                    str(failure.summary)
                )[:200],
                "log_artifact_key": _safe_log_artifact_key(
                    failure.log_artifact_key
                ),
            }
        )
    history.append(record)
    payload["repair_history"] = history[-10:]


def _normalize_repair_history_record(raw):  # type: ignore[no-untyped-def]
    from tutor.services.manim_render.executor import sanitize_public_diagnostic

    try:
        failed_revision = max(0, int(raw.get("failed_revision") or 0))
    except (TypeError, ValueError):
        failed_revision = 0
    record = {
        "job_id": sanitize_public_diagnostic(str(raw.get("job_id") or ""))[:96],
        "failed_revision": failed_revision,
        "status": str(raw.get("status") or "failed")[:20],
    }
    if raw.get("error_code"):
        record["error_code"] = sanitize_public_diagnostic(
            str(raw["error_code"])
        )[:120]
    if raw.get("summary"):
        record["summary"] = sanitize_public_diagnostic(str(raw["summary"]))[:200]
    safe_log_key = _safe_log_artifact_key(raw.get("log_artifact_key"))
    if safe_log_key:
        record["log_artifact_key"] = safe_log_key
    return record


def _safe_log_artifact_key(value) -> str:  # type: ignore[no-untyped-def]
    from pathlib import Path

    from tutor.services.artifacts import UnsafeArtifactKey, resolve_artifact_key

    key = str(value or "")
    if not key.startswith("manim_logs/"):
        return ""
    try:
        resolve_artifact_key(key, Path("."))
    except UnsafeArtifactKey:
        return ""
    return key


class ProfileUpdateFollowUpCapability(BaseCapability):
    """Aggregate a stable learning-event window without invoking an LLM."""

    manifest = CapabilityManifest(
        name="profile_update",
        description="内部确定性学习画像更新子任务",
        stages=["profile_update"],
        tags=["internal", "follow_up", "profile"],
    )

    def __init__(self, *, event_store=None, profile_store=None, builder=None) -> None:
        super().__init__()
        self._event_store = event_store
        self._profile_store = profile_store
        self._builder = builder

    async def run(self, context: UnifiedContext, stream: StreamBus) -> CapabilityResult:
        from tutor.services.learner_profile.builder import ProfileBuilder
        from tutor.services.learner_profile.schema import empty_profile
        from tutor.services.learner_profile.store import get_profile_store
        from tutor.services.learning_events.store import get_learning_event_store

        event_store = self._event_store or get_learning_event_store()
        profile_store = self._profile_store or get_profile_store()
        builder = self._builder or ProfileBuilder(store=profile_store)
        start = int(context.metadata["from_watermark"])
        through = int(context.metadata["through_sequence"])
        if through <= start:
            raise ValueError("profile event window must advance")
        await _require_current_claim(context)
        events = await event_store.list_since(
            context.user_id, start, through_sequence=through
        )
        window_course = next(
            (event.course for event in reversed(events) if event.course),
            str(context.metadata.get("course") or ""),
        )
        current = await profile_store.get(context.user_id)
        current = current or empty_profile(context.user_id)
        if current.event_watermark < through:
            if current.event_watermark != start:
                raise RuntimeError("profile watermark is stale")
            for _attempt in range(3):
                candidate = builder.aggregate_events(
                    current, events, through_sequence=through
                )
                outcome = None

                async def persist_profile(candidate_to_save=candidate) -> None:
                    nonlocal outcome
                    outcome = await profile_store.save_event_profile(
                        candidate_to_save, expected_watermark=start
                    )

                guard = context.metadata.get("_claim_guard")
                if callable(guard):
                    if not await guard(persist_profile):
                        raise PermissionError("follow-up claim is no longer current")
                else:
                    await persist_profile()
                if outcome is None:
                    raise RuntimeError("profile persistence failed")
                current = outcome.profile
                if current.event_watermark >= through:
                    break
                if current.event_watermark != start:
                    raise RuntimeError("profile watermark is stale")
            else:
                raise RuntimeError("profile changed repeatedly during event aggregation")

        follow_ups: list[FollowUpTaskSpec] = []
        if await profile_store.get_path(context.user_id, current.version) is None:
            follow_ups.append(
                FollowUpTaskSpec(
                    kind="path_rebuild",
                    dedupe_key=f"path_rebuild:{current.version}",
                    payload={
                        "user_id": context.user_id,
                        "profile_version": current.version,
                        "profile": current.model_dump(mode="json"),
                        "course": window_course,
                    },
                )
            )
        next_through = await event_store.profile_trigger_sequence_since(
            context.user_id,
            current.event_watermark,
        )
        if next_through is not None:
            pending_events = await event_store.list_since(
                context.user_id,
                current.event_watermark,
                through_sequence=next_through,
            )
            next_course = next(
                (event.course for event in reversed(pending_events) if event.course),
                window_course,
            )
            follow_ups.append(
                FollowUpTaskSpec(
                    kind="profile_update",
                    dedupe_key=f"profile_update:{current.event_watermark}",
                    payload={
                        "user_id": context.user_id,
                        "from_watermark": current.event_watermark,
                        "through_sequence": next_through,
                        "course": next_course,
                    },
                )
            )
        return CapabilityResult(
            assistant_message="学习者画像已更新",
            payload={
                "profile_version": current.version,
                "event_watermark": current.event_watermark,
                "knowledge_scores": dict(current.knowledge_map.scores),
            },
            follow_up_tasks=tuple(follow_ups),
        )


class PathRebuildFollowUpCapability(BaseCapability):
    """Plan and persist a path for the exact profile snapshot in the child."""

    manifest = CapabilityManifest(
        name="path_rebuild",
        description="内部画像版本绑定学习路径子任务",
        stages=["path_rebuild"],
        tags=["internal", "follow_up", "path"],
    )

    def __init__(self, *, profile_store=None, kg_service=None) -> None:
        super().__init__()
        self._profile_store = profile_store
        self._kg_service = kg_service

    async def run(self, context: UnifiedContext, stream: StreamBus) -> CapabilityResult:
        from tutor.services.knowledge_graph import get_knowledge_graph_service
        from tutor.services.knowledge_graph.planner import KGPathPlanner
        from tutor.services.learner_profile.schema import (
            LearnerProfile,
            PersistedLearningPath,
        )
        from tutor.services.learner_profile.store import get_profile_store

        store = self._profile_store or get_profile_store()
        version = int(context.metadata["profile_version"])
        profile = LearnerProfile.model_validate(context.metadata["profile"])
        if profile.user_id != context.user_id or profile.version != version:
            raise ValueError("path profile snapshot does not match child identity")
        existing = await store.get_path(context.user_id, version)
        if existing is not None:
            return CapabilityResult(
                assistant_message="学习路径已恢复",
                payload=existing.model_dump(mode="json"),
            )

        service = self._kg_service or get_knowledge_graph_service()
        course = str(context.metadata.get("course") or service.default_course())
        nodes: list[dict] = []
        edges: list[dict] = []
        rationale = "knowledge graph has no learnable nodes"
        path_id = f"profile-{version}"
        name = ""
        description = ""
        total_hours = 0.0
        completed_count = 0
        available_count = 0
        locked_count = 0
        if course and service.has_course(course):
            model, graph = service.get_graph(course)
            planned = service.plan_for_learner(course, profile)
            if model.nodes and not any(
                node.node_id in model.node_ids() for node in planned.nodes
            ):
                planned = KGPathPlanner().plan(
                    model,
                    graph,
                    profile,
                    path_id="__automatic_graph_fallback__",
                )
            selected = {node.node_id for node in planned.nodes}
            nodes = [
                {
                    "id": node.node_id,
                    "name": node.name,
                    "category": node.category,
                    "difficulty": node.difficulty,
                    "estimated_hours": node.estimated_hours,
                    "prerequisites": model.prerequisites_of(node.node_id),
                    "status": node.status.value,
                }
                for node in planned.nodes
                if node.node_id in model.node_ids()
            ]
            edges = [
                {"from": edge.from_, "to": edge.to, "type": edge.type.value}
                for edge in model.edges
                if edge.from_ in selected and edge.to in selected
            ]
            rationale = "mastery-aware prerequisite topological order"
            path_id = planned.path_id or path_id
            name = planned.name
            description = planned.description
            total_hours = planned.total_estimated_hours
            completed_count = planned.completed_count
            available_count = planned.available_count
            locked_count = planned.locked_count
        path = PersistedLearningPath(
            user_id=context.user_id,
            profile_version=version,
            course=course,
            path_id=path_id,
            name=name,
            description=description,
            nodes=nodes,
            edges=edges,
            rationale=rationale,
            total_estimated_hours=total_hours,
            completed_count=completed_count,
            available_count=available_count,
            locked_count=locked_count,
        )
        persisted = None

        async def persist_path() -> None:
            nonlocal persisted
            persisted = await store.save_path(path)

        await _require_current_claim(context)
        guard = context.metadata.get("_claim_guard")
        if callable(guard):
            if not await guard(persist_path):
                raise PermissionError("follow-up claim is no longer current")
        else:
            await persist_path()
        if persisted is None:
            raise RuntimeError("path persistence failed")
        return CapabilityResult(
            assistant_message=("学习路径已生成" if persisted.nodes else "暂无可规划知识节点"),
            payload=persisted.model_dump(mode="json"),
        )


_FOLLOW_UP_BUILDERS = {
    "video_render": VideoRenderFollowUpCapability,
    "video_repair_render": VideoRepairFollowUpCapability,
    "profile_update": ProfileUpdateFollowUpCapability,
    "path_rebuild": PathRebuildFollowUpCapability,
}


def validate_follow_up_spec(spec: FollowUpTaskSpec) -> None:
    """Reject unsupported or malformed internal work before persistence."""
    if spec.kind not in _FOLLOW_UP_BUILDERS:
        raise ValueError(f"unsupported follow-up kind: {spec.kind}")
    if not isinstance(spec.payload, dict):
        raise ValueError("follow-up payload must be an object")
    if not isinstance(spec.dedupe_key, str) or not spec.dedupe_key.strip():
        raise ValueError("follow-up dedupe_key must be non-empty")
    if len(spec.dedupe_key) > 256:
        raise ValueError("follow-up dedupe_key exceeds 256 characters")
    if spec.kind in {"video_render", "video_repair_render"}:
        required = (
            ("package_id", "resource_id")
            if spec.kind == "video_render"
            else ("package_id", "resource_id", "user_id", "failed_revision")
        )
        for field in required:
            value = spec.payload.get(field)
            if field == "failed_revision":
                if type(value) is not int or value < 0:
                    raise ValueError(
                        "video_repair_render follow-up requires non-negative failed_revision"
                    )
            elif not isinstance(value, str) or not value.strip():
                raise ValueError(f"{spec.kind} follow-up requires {field}")
    elif spec.kind == "profile_update":
        for field in ("user_id", "from_watermark", "through_sequence"):
            if field not in spec.payload:
                raise ValueError(f"profile_update follow-up requires {field}")
    elif spec.kind == "path_rebuild":
        for field in ("user_id", "profile_version", "profile"):
            if field not in spec.payload:
                raise ValueError(f"path_rebuild follow-up requires {field}")


def build_follow_up_capability(task_kind: str) -> BaseCapability | None:
    builder = _FOLLOW_UP_BUILDERS.get(task_kind)
    return builder() if builder is not None else None


__all__ = [
    "FollowUpScheduler",
    "VideoRenderFollowUpCapability",
    "VideoRepairFollowUpCapability",
    "ProfileUpdateFollowUpCapability",
    "PathRebuildFollowUpCapability",
    "build_follow_up_capability",
    "validate_follow_up_spec",
]
