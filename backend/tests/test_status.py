"""Tests del esqueleto del BE: healthcheck bajo el contrato AD-1 (`/api/v1/status`)."""
from fastapi.testclient import TestClient

from wakemeup import __version__
from wakemeup.api import app


def test_status_ok() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
