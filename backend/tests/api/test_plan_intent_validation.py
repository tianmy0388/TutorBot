from __future__ import annotations

import pytest
import tutor.api.routers.plans as plans_module
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clear_plans() -> None:
    plans_module._PLANS.clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(plans_module.router, prefix="/api/v1")
    return TestClient(app)


@pytest.mark.parametrize("explicit", ["admin", "resource_generaton"])
def test_plan_endpoint_rejects_invalid_explicit_capability_without_creating_plan(
    explicit: str,
) -> None:
    response = _client().post(
        "/api/v1/plans",
        json={
            "message": "生成一份学习资源",
            "explicit_capability": explicit,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_CAPABILITY"
    assert plans_module._PLANS == {}


def test_plan_endpoint_preserves_valid_resource_generation_hint() -> None:
    response = _client().post(
        "/api/v1/plans",
        json={
            "message": "解释注意力机制",
            "explicit_capability": "resource_generation",
        },
    )

    assert response.status_code == 200
    assert response.json()["intent"] == "resource_generation"
    assert len(plans_module._PLANS) == 1
