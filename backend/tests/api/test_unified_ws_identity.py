from __future__ import annotations

from types import SimpleNamespace

import pytest
import tutor.api.routers.unified_ws as ws_module
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _JobStore:
    def __init__(self, owners: dict[str, str]) -> None:
        self._owners = owners

    async def get(self, job_id: str):
        owner = self._owners.get(job_id)
        return None if owner is None else SimpleNamespace(job_id=job_id, user_id=owner)


class _Runner:
    def __init__(self) -> None:
        self.subscriptions: list[str] = []

    async def subscribe(self, job_id: str):
        self.subscriptions.append(job_id)
        yield {"type": "done", "job_id": job_id}


def _client(monkeypatch, *, multi_user_enabled: bool, owners: dict[str, str]):
    runner = _Runner()
    store = _JobStore(owners)
    monkeypatch.setattr(ws_module, "get_job_runner", lambda: runner)
    monkeypatch.setattr(ws_module, "get_job_store", lambda: store, raising=False)
    app = FastAPI()
    app.state.settings = SimpleNamespace(multi_user_enabled=multi_user_enabled)
    app.include_router(ws_module.router, prefix="/api/v1")
    return TestClient(app), runner


def test_subscribe_allows_own_job_in_multi_user_mode(monkeypatch) -> None:
    client, runner = _client(
        monkeypatch,
        multi_user_enabled=True,
        owners={"job-own": "u_alice"},
    )

    with client.websocket_connect("/api/v1/ws") as websocket:
        websocket.send_json({"type": "subscribe_job", "job_id": "job-own", "user_id": "u_alice"})
        response = websocket.receive_json()

    assert response == {"type": "ack", "for": "subscribe_job", "job_id": "job-own"}
    assert runner.subscriptions == ["job-own"]


def test_subscribe_requires_identity_in_multi_user_mode(monkeypatch) -> None:
    client, runner = _client(
        monkeypatch,
        multi_user_enabled=True,
        owners={"job-own": "u_alice"},
    )

    with client.websocket_connect("/api/v1/ws") as websocket:
        websocket.send_json({"type": "subscribe_job", "job_id": "job-own"})
        response = websocket.receive_json()

    assert response["type"] == "error"
    assert "user_id is required" in response["content"]
    assert runner.subscriptions == []


def test_subscribe_hides_cross_owner_job_like_missing_job(monkeypatch) -> None:
    client, runner = _client(
        monkeypatch,
        multi_user_enabled=True,
        owners={"job-alice": "u_alice"},
    )

    responses = []
    for job_id in ("job-alice", "job-missing"):
        with client.websocket_connect("/api/v1/ws") as websocket:
            websocket.send_json({"type": "subscribe_job", "job_id": job_id, "user_id": "u_bob"})
            responses.append(websocket.receive_json())

    assert responses == [
        {"type": "error", "content": "job not found"},
        {"type": "error", "content": "job not found"},
    ]
    assert runner.subscriptions == []


@pytest.mark.parametrize("requested_user_id", [None, "u_stale_browser"])
def test_subscribe_resolves_local_identity(monkeypatch, requested_user_id) -> None:
    client, runner = _client(
        monkeypatch,
        multi_user_enabled=False,
        owners={"job-local": "local-user"},
    )
    envelope = {"type": "subscribe_job", "job_id": "job-local"}
    if requested_user_id is not None:
        envelope["user_id"] = requested_user_id

    with client.websocket_connect("/api/v1/ws") as websocket:
        websocket.send_json(envelope)
        response = websocket.receive_json()

    assert response == {"type": "ack", "for": "subscribe_job", "job_id": "job-local"}
    assert runner.subscriptions == ["job-local"]
