from __future__ import annotations

import pytest
from tutor.api import main as main_module
from tutor.services.config.settings import Settings


@pytest.mark.asyncio
async def test_create_app_lifespan_owns_learning_services_under_injected_settings(
    tmp_path,
    monkeypatch,
) -> None:
    injected_dir = tmp_path / "injected-data"
    forbidden_global_dir = tmp_path / "forbidden-global-data"
    injected_kb_dir = tmp_path / "injected-kb"
    injected_course = injected_kb_dir / "injected-course"
    injected_course.mkdir(parents=True)
    (injected_course / "knowledge_graph.yaml").write_text("nodes: []\n")
    global_kb_dir = tmp_path / "global-kb"
    global_course = global_kb_dir / "global-course"
    global_course.mkdir(parents=True)
    (global_course / "knowledge_graph.yaml").write_text("nodes: []\n")
    injected = Settings(
        env="test",
        data_dir=injected_dir,
        kb_dir=injected_kb_dir,
        kb_default="injected-course",
    )
    global_settings = Settings(
        env="test",
        data_dir=forbidden_global_dir,
        kb_dir=global_kb_dir,
        kb_default="global-course",
    )
    monkeypatch.setattr(main_module, "get_settings", lambda: global_settings)

    app = main_module.create_app(injected)
    async with app.router.lifespan_context(app):
        assert app.state.settings is injected
        workflow = app.state.learning_workflow
        assert workflow.event_store.db_path == injected_dir / "learning_events.db"
        assert workflow.profile_store.db_path == injected_dir / "profiles.db"
        assert workflow.job_store.db_path == injected_dir / "jobs.db"
        assert app.state.learning_runner.store is workflow.job_store
        kg_service = app.state.knowledge_graph_service
        assert kg_service.loader.kb_dir == injected_kb_dir
        assert kg_service.default_course() == "injected-course"
        assert app.state.capabilities.get("path_planning")._kg_service is kg_service
        tutoring = app.state.capabilities.get("tutoring")
        assert tutoring is not None
        assert tutoring.event_store is workflow.event_store
        path_follow_up = app.state.learning_runner._follow_up_builder("path_rebuild")
        assert path_follow_up._kg_service is kg_service
        assert workflow.event_store._engine is not None
        assert workflow.profile_store._engine is not None
        assert workflow.job_store._engine is not None
        assert not forbidden_global_dir.exists()
        fallback_dir = tmp_path / "data"
        if fallback_dir.exists():
            assert not list(fallback_dir.rglob("*.db"))

    assert workflow.event_store._engine is None
    assert workflow.profile_store._engine is None
    assert workflow.job_store._engine is None


@pytest.mark.asyncio
async def test_lifespan_closes_initialized_stores_when_startup_fails(
    tmp_path,
    monkeypatch,
) -> None:
    app = main_module.create_app(Settings(env="test", data_dir=tmp_path / "owned"))
    workflow = app.state.learning_workflow

    async def fail_event_init() -> None:
        raise RuntimeError("private startup failure")

    monkeypatch.setattr(workflow.event_store, "init", fail_event_init)
    with pytest.raises(RuntimeError, match="private startup failure"):
        async with app.router.lifespan_context(app):
            pass

    assert workflow.profile_store._engine is None
    assert workflow.event_store._engine is None
    assert workflow.job_store._engine is None
    assert app.state.resource_package_store._engine is None


@pytest.mark.asyncio
async def test_lifespan_closes_each_store_when_another_close_fails(
    tmp_path,
    monkeypatch,
) -> None:
    app = main_module.create_app(Settings(env="test", data_dir=tmp_path / "owned"))
    workflow = app.state.learning_workflow

    async def fail_event_close() -> None:
        raise RuntimeError("private close failure")

    monkeypatch.setattr(workflow.event_store, "close", fail_event_close)
    async with app.router.lifespan_context(app):
        assert workflow.profile_store._engine is not None

    assert workflow.profile_store._engine is None
    assert workflow.job_store._engine is None
    assert app.state.resource_package_store._engine is None
