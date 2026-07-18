"""FastAPI application factory.

This module wires together:

- The :class:`MainOrchestrator`
- Capability and tool registries (loaded eagerly)
- WebSocket endpoint at ``/api/v1/ws``
- HTTP routers (health, sessions, profile, knowledge, resources)
- CORS middleware (development-friendly defaults)

Design inspired by DeepTutor's ``deeptutor/api/main.py``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from tutor import __version__
from tutor.runtime import CapabilityRegistry, MainOrchestrator, get_tool_registry
from tutor.services.config.settings import Settings, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown hooks."""
    # Push .env into os.environ. pydantic-settings reads its own copy when
    # it builds ``Settings``, but the MCP config loader substitutes
    # ``${VAR}`` references from ``os.environ`` only — without this, keys
    # defined in .env (e.g. MINIMAX_API_KEY) would be missing when the
    # subprocess is spawned.
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        for candidate in (Path.cwd() / ".env", Path.cwd().parent / ".env"):
            if candidate.is_file():
                load_dotenv(candidate, override=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"load_dotenv skipped: {exc!r}")

    settings = app.state.settings

    workflow = app.state.learning_workflow
    resource_store = app.state.resource_package_store
    attempt_store = app.state.exercise_attempt_store
    kg_service = app.state.knowledge_graph_service
    from tutor.capabilities.assessment import AssessmentCapability
    from tutor.capabilities.path_planning import PathPlanningCapability
    from tutor.capabilities.profile import LearnerProfileCapability
    from tutor.capabilities.resource_generation import ResourceGenerationCapability
    from tutor.capabilities.tutoring import TutoringCapability
    from tutor.services.learner_profile.builder import ProfileBuilder

    profile_builder = ProfileBuilder(store=workflow.profile_store)
    capabilities = CapabilityRegistry()
    for capability in (
        LearnerProfileCapability(builder=profile_builder),
        ResourceGenerationCapability(
            builder=profile_builder,
            package_store=resource_store,
            settings=settings,
        ),
        PathPlanningCapability(
            profile_store=workflow.profile_store,
            kg_service=kg_service,
        ),
        TutoringCapability(builder=profile_builder),
        AssessmentCapability(
            builder=profile_builder,
            event_store=workflow.event_store,
        ),
    ):
        capabilities.register(capability)
    tools = get_tool_registry()
    orchestrator = MainOrchestrator(capability_registry=capabilities)

    logger.info(f"Tutor v{__version__} starting up")
    logger.info(f"  environment: {settings.env}")
    logger.info(f"  language:    {settings.language}")
    logger.info(f"  llm:         {settings.llm_provider} / {settings.llm_model}")
    logger.info(f"  capabilities: {capabilities.list_capabilities()}")
    logger.info(f"  tools:        {tools.list_tools()}")

    # Stash on app.state for easy access from endpoints
    app.state.capabilities = capabilities
    app.state.tools = tools
    app.state.orchestrator = orchestrator

    from tutor.services.jobs.follow_up import (
        PathRebuildFollowUpCapability,
        ProfileUpdateFollowUpCapability,
        VideoRenderFollowUpCapability,
        build_follow_up_capability,
    )
    from tutor.services.jobs.runner import JobRunner

    def build_owned_follow_up(task_kind: str):
        if task_kind == "profile_update":
            return ProfileUpdateFollowUpCapability(
                event_store=workflow.event_store,
                profile_store=workflow.profile_store,
            )
        if task_kind == "path_rebuild":
            return PathRebuildFollowUpCapability(
                profile_store=workflow.profile_store,
                kg_service=kg_service,
            )
        if task_kind == "video_render":
            return VideoRenderFollowUpCapability(
                package_store=resource_store,
                settings=settings,
            )
        return build_follow_up_capability(task_kind)

    runner = JobRunner(
        job_store=workflow.job_store,
        capability_registry=capabilities,
        follow_up_builder=build_owned_follow_up,
    )
    app.state.learning_runner = runner

    try:
        # The application owns this persistence graph. Startup is inside
        # the cleanup boundary so partial initialisation cannot leak engines.
        await workflow.profile_store.init()
        await workflow.event_store.init()
        await workflow.job_store.init()
        await resource_store.init()
        await attempt_store.init()
        await attempt_store.reap_orphaned_claims()
        from tutor.services.exercise_attempts.publisher import (
            repair_unpublished_attempt_events,
        )

        await repair_unpublished_attempt_events(
            attempt_store=attempt_store,
            workflow=workflow,
        )
        await workflow.reconcile_all()
        await runner.resume_active_jobs()
        logger.info("Application-owned learning stores initialised")
        yield
    finally:
        logger.info("Tutor shutting down")
        try:
            await runner.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "JOB_RUNNER_SHUTDOWN_FAILED exception_type={}",
                type(exc).__name__,
            )
        # Tear down MCP subprocesses (started lazily by MCPRegistry on
        # first web_search / understand_image call) so they don't outlive
        # the API process.
        try:
            from tutor.services.mcp import get_mcp_registry

            await get_mcp_registry().stop_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "MCP_SHUTDOWN_FAILED exception_type={}",
                type(exc).__name__,
            )
        for code, close in (
            ("LEARNING_EVENT_STORE_CLOSE_FAILED", workflow.event_store.close),
            ("PROFILE_STORE_CLOSE_FAILED", workflow.profile_store.close),
            ("JOB_STORE_CLOSE_FAILED", workflow.job_store.close),
            ("RESOURCE_STORE_CLOSE_FAILED", resource_store.close),
            ("EXERCISE_ATTEMPT_STORE_CLOSE_FAILED", attempt_store.close),
        ):
            try:
                await close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("{} exception_type={}", code, type(exc).__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return a FastAPI application."""
    settings = settings or get_settings()

    app = FastAPI(
        title="TutorBot — Multi-Agent Learning System",
        version=__version__,
        description=(
            "Tutor is a multi-agent AI system that generates personalized "
            "multi-modal learning resources for higher-education students."
        ),
        lifespan=lifespan,
    )
    # Available before lifespan startup as well (notably to ASGI test
    # transports and identity dependencies).
    app.state.settings = settings
    from tutor.services.exercise_attempts.store import ExerciseAttemptStore
    from tutor.services.jobs.store import JobStore
    from tutor.services.knowledge_graph.loader import KnowledgeGraphLoader
    from tutor.services.knowledge_graph.service import KnowledgeGraphService
    from tutor.services.learner_profile.store import ProfileStore
    from tutor.services.learning_events.store import LearningEventStore
    from tutor.services.learning_events.workflow import LearningWorkflow
    from tutor.services.resource_package.store import ResourcePackageStore

    event_store = LearningEventStore(settings.data_dir / "learning_events.db")
    profile_store = ProfileStore(settings.data_dir / "profiles.db")
    job_store = JobStore(settings.data_dir / "jobs.db")
    app.state.learning_workflow = LearningWorkflow(
        event_store=event_store,
        profile_store=profile_store,
        job_store=job_store,
    )
    app.state.resource_package_store = ResourcePackageStore(
        settings.data_dir / "resource_packages.db"
    )
    app.state.exercise_attempt_store = ExerciseAttemptStore(
        settings.data_dir / "exercise_attempts.db"
    )
    app.state.knowledge_graph_service = KnowledgeGraphService(
        loader=KnowledgeGraphLoader(settings.kb_dir),
        default_course=settings.kb_default,
    )
    app.state.learning_runner = None

    # CORS — development friendly; restrict in production
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers
    # **2026-07-09 fix (ada95ede trace):** mount the manim public
    # directory at ``/static/manim/`` so the URLs produced by
    # ``ManimRenderService._publish`` (``/static/manim/<scene>.mp4``)
    # actually resolve. Pre-fix, the service wrote the MP4 to
    # ``data/manim_videos/`` and returned that URL, but no route
    # served it — the right pane rendered a 0-second blank ``<video>``.
    # We mount under the *same* ``data_dir/manim_videos`` directory
    # the service uses (anchored to an absolute path so cwd-relative
    # surprises don't split the served dir from the publish dir).
    try:
        manim_videos_dir = settings.data_dir / "manim_videos"
        manim_videos_dir.mkdir(parents=True, exist_ok=True)
        # ``check_dir=False`` is critical: the service may publish a
        # brand-new file *after* startup. StaticFiles re-scans on
        # each request anyway.
        app.mount(
            "/static/manim",
            StaticFiles(directory=str(manim_videos_dir), check_dir=False),
            name="manim-videos",
        )
        logger.info(f"Manim videos mounted at /static/manim → {manim_videos_dir}")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Could not mount /static/manim (non-fatal): {exc!r}")

    # Routers
    from tutor.api.routers.config import router as config_router
    from tutor.api.routers.conversations import router as conversations_router
    from tutor.api.routers.courses import router as courses_router
    from tutor.api.routers.exercises import router as exercises_router
    from tutor.api.routers.health import router as health_router
    from tutor.api.routers.jobs import router as jobs_router
    from tutor.api.routers.knowledge_bases import router as kb_router
    from tutor.api.routers.knowledge_graph import router as kg_router
    from tutor.api.routers.learning import router as learning_router
    from tutor.api.routers.plans import router as plans_router
    from tutor.api.routers.resources import router as resources_router
    from tutor.api.routers.unified_ws import router as ws_router

    app.include_router(health_router, prefix="/api/v1", tags=["health"])
    app.include_router(kg_router, prefix="/api/v1", tags=["knowledge-graph"])
    app.include_router(kb_router, prefix="/api/v1", tags=["knowledge-bases"])
    app.include_router(courses_router, prefix="/api/v1", tags=["courses"])
    app.include_router(resources_router, prefix="/api/v1", tags=["resources"])
    app.include_router(jobs_router, prefix="/api/v1", tags=["jobs"])
    app.include_router(plans_router, prefix="/api/v1", tags=["plans"])
    app.include_router(config_router, prefix="/api/v1", tags=["config"])
    app.include_router(conversations_router, prefix="/api/v1", tags=["conversations"])
    app.include_router(learning_router, prefix="/api/v1", tags=["learning"])
    app.include_router(exercises_router, prefix="/api/v1", tags=["exercises"])
    # Compatibility alias for the documented non-versioned learning API.
    app.include_router(
        learning_router,
        prefix="/api",
        tags=["learning"],
        include_in_schema=False,
    )
    app.include_router(
        exercises_router,
        prefix="/api",
        tags=["exercises"],
        include_in_schema=False,
    )
    app.include_router(ws_router, prefix="/api/v1", tags=["websocket"])

    # Root info
    @app.get("/", include_in_schema=False)
    async def root() -> JSONResponse:
        return JSONResponse(
            {
                "name": "Tutor",
                "version": __version__,
                "docs": "/docs",
                "health": "/api/v1/health",
                "websocket": "/api/v1/ws",
            }
        )

    return app


# Module-level instance for `uvicorn tutor.api.main:app`
app = create_app()


__all__ = ["app", "create_app"]
