"""Competition demo endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from tutor.demo import DemoScenarioNotFound, get_demo_service
from tutor.demo.schema import DemoLoadRequest, DemoLoadResult, DemoScenario

router = APIRouter()


@router.get("/demo/scenarios")
async def list_demo_scenarios() -> dict[str, list[DemoScenario]]:
    """List deterministic scenarios available for the competition demo."""
    return {"items": get_demo_service().list_scenarios()}


@router.post("/demo/scenarios/{scenario_id}/load")
async def load_demo_scenario(
    scenario_id: str,
    request: DemoLoadRequest | None = None,
) -> DemoLoadResult:
    """Load a scenario snapshot and optionally persist it."""
    try:
        return await get_demo_service().load_scenario(
            scenario_id,
            request or DemoLoadRequest(),
        )
    except DemoScenarioNotFound:
        raise HTTPException(status_code=404, detail="demo scenario not found")


__all__ = ["router"]
