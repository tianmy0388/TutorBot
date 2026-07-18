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
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from tutor import __version__
from tutor.runtime import get_capability_registry, get_orchestrator, get_tool_registry
from tutor.runtime.orchestrator import MainOrchestrator
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
        from dotenv import load_dotenv
        from pathlib import Path

        for candidate in (Path.cwd() / ".env", Path.cwd().parent / ".env"):
            if candidate.is_file():
                load_dotenv(candidate, override=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"load_dotenv skipped: {exc!r}")

    settings = get_settings()

    # Eager-load singletons
    capabilities = get_capability_registry()
    tools = get_tool_registry()
    orchestrator = get_orchestrator()

    logger.info(f"Tutor v{__version__} starting up")
    logger.info(f"  environment: {settings.env}")
    logger.info(f"  language:    {settings.language}")
    logger.info(f"  llm:         {settings.llm_provider} / {settings.llm_model}")
    logger.info(f"  capabilities: {capabilities.list_capabilities()}")
    logger.info(f"  tools:        {tools.list_tools()}")

    # Stash on app.state for easy access from endpoints
    app.state.settings = settings
    app.state.capabilities = capabilities
    app.state.tools = tools
    app.state.orchestrator = orchestrator

    # Initialise persistent services (create SQLite tables, etc.)
    try:
        from tutor.services.learner_profile.builder import get_profile_builder

        await get_profile_builder().initialize()
        logger.info("ProfileStore initialised")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"ProfileStore init failed: {exc!r}")

    try:
        from tutor.services.resource_package.store import get_resource_package_store

        await get_resource_package_store().init()
        logger.info("ResourcePackageStore initialised")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"ResourcePackageStore init failed: {exc!r}")

    try:
        from tutor.services.learning_events.store import get_learning_event_store

        await get_learning_event_store().init()
        logger.info("LearningEventStore initialised")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"LearningEventStore init failed: {exc!r}")

    try:
        from tutor.services.jobs import get_job_runner, get_job_store

        await get_job_store().init()
        logger.info("JobStore initialised")

        # On restart, mark any in-flight jobs as failed so they don't
        # block the UI (the asyncio tasks are gone).
        await get_job_runner().resume_active_jobs()
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"JobStore init failed: {exc!r}")

    # 2026-06-21 plan: persistent KB and Course stores. The KB
    # store walks the on-disk layout on first init() to migrate any
    # orphan files the in-memory store had been tracking; the
    # course store then re-binds the seeded AI 导论 course.
    # We do this lazily — the kb router's module-level
    # ``KnowledgeBaseService()`` constructor already triggers the
    # first init, and the courses store opens on first call to
    # ``get_course_service()``. Wrapping that work here is just a
    # chance to log a clean "ready" message.
    try:
        from tutor.services.knowledge_base.sqlite_store import (
            get_kb_store,
        )
        from tutor.services.courses import (
            get_course_service,
        )

        get_kb_store()
        get_course_service()
        logger.info("KBStore + CourseStore initialised")
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"KB/Course store init failed: {exc!r}")

    try:
        yield
    finally:
        logger.info("Tutor shutting down")
        # Tear down MCP subprocesses (started lazily by MCPRegistry on
        # first web_search / understand_image call) so they don't outlive
        # the API process.
        try:
            from tutor.services.mcp import get_mcp_registry

            await get_mcp_registry().stop_all()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"MCP shutdown failed (non-fatal): {exc!r}")


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
        from starlette.staticfiles import StaticFiles as _SM  # noqa: F401

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
    from tutor.api.routers.demo import router as demo_router
    from tutor.api.routers.health import router as health_router
    from tutor.api.routers.jobs import router as jobs_router
    from tutor.api.routers.learning_events import router as learning_events_router
    from tutor.api.routers.knowledge_bases import router as kb_router
    from tutor.api.routers.knowledge_graph import router as kg_router
    from tutor.api.routers.plans import router as plans_router
    from tutor.api.routers.resources import router as resources_router
    from tutor.api.routers.teacher import router as teacher_router
    from tutor.api.routers.unified_ws import router as ws_router

    app.include_router(health_router, prefix="/api/v1", tags=["health"])
    app.include_router(kg_router, prefix="/api/v1", tags=["knowledge-graph"])
    app.include_router(kb_router, prefix="/api/v1", tags=["knowledge-bases"])
    app.include_router(courses_router, prefix="/api/v1", tags=["courses"])
    app.include_router(demo_router, prefix="/api/v1", tags=["demo"])
    app.include_router(resources_router, prefix="/api/v1", tags=["resources"])
    app.include_router(jobs_router, prefix="/api/v1", tags=["jobs"])
    app.include_router(learning_events_router, prefix="/api/v1", tags=["learning-events"])
    app.include_router(plans_router, prefix="/api/v1", tags=["plans"])
    app.include_router(config_router, prefix="/api/v1", tags=["config"])
    app.include_router(conversations_router, prefix="/api/v1", tags=["conversations"])
    app.include_router(teacher_router, prefix="/api/v1", tags=["teacher"])
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
