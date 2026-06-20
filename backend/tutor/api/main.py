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
from loguru import logger

from tutor import __version__
from tutor.runtime import get_capability_registry, get_orchestrator, get_tool_registry
from tutor.runtime.orchestrator import MainOrchestrator
from tutor.services.config.settings import Settings, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown hooks."""
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
        from tutor.services.jobs import get_job_runner, get_job_store

        await get_job_store().init()
        logger.info("JobStore initialised")

        # On restart, mark any in-flight jobs as failed so they don't
        # block the UI (the asyncio tasks are gone).
        await get_job_runner().resume_active_jobs()
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"JobStore init failed: {exc!r}")

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
        title="Tutor — Multi-Agent Learning System",
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
    from tutor.api.routers.health import router as health_router
    from tutor.api.routers.jobs import router as jobs_router
    from tutor.api.routers.knowledge_graph import router as kg_router
    from tutor.api.routers.resources import router as resources_router
    from tutor.api.routers.unified_ws import router as ws_router

    app.include_router(health_router, prefix="/api/v1", tags=["health"])
    app.include_router(kg_router, prefix="/api/v1", tags=["knowledge-graph"])
    app.include_router(resources_router, prefix="/api/v1", tags=["resources"])
    app.include_router(jobs_router, prefix="/api/v1", tags=["jobs"])
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
