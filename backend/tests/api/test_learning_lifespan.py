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
    injected = Settings(env="test", data_dir=injected_dir)
    global_settings = Settings(env="test", data_dir=forbidden_global_dir)
    monkeypatch.setattr(main_module, "get_settings", lambda: global_settings)

    app = main_module.create_app(injected)
    async with app.router.lifespan_context(app):
        assert app.state.settings is injected
        workflow = app.state.learning_workflow
        assert workflow.event_store.db_path == injected_dir / "learning_events.db"
        assert workflow.profile_store.db_path == injected_dir / "profiles.db"
        assert workflow.job_store.db_path == injected_dir / "jobs.db"
        assert app.state.learning_runner.store is workflow.job_store
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
