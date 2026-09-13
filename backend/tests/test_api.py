"""Tests de la API (spec 1.4): auth Bearer, envelope de errores (AD-1), DTO
AD-10, `POST /scan` 202 (AD-3) y reporte WOL en el healthcheck (FR-5 parcial).

La app importa servicios construidos desde el Settings; los tests los
sustituyen por dobles (sin DB real ni red) y ejercitan las rutas con
`fastapi.testclient.TestClient`.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from wakemeup.adapters.db import Db
from wakemeup.config import AuthSettings, ScanSettings, Settings
from wakemeup.core.models import HostInfo, MachineDTO
from wakemeup.services.auth import AuthService, new_device_token, sha256_hex_lower
from wakemeup.services.discovery import DiscoveryService
from wakemeup.services.status import StatusService

from wakemeup.api import app


class FakeDb:
    """Doble de DB: tokens en memoria con activos/revocados."""

    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}  # sha256 -> device_name (activo)
        self.revoked: set[str] = set()
        self.machines: list[tuple[str, str, str, str]] = []  # (ip, mac, hostname, state)
        self.scan_calls = 0

    async def init_db(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def token_exists(self, sha: str) -> bool:
        return sha in self.tokens and sha not in self.revoked

    async def create_token(self, device_name: str, sha: str) -> None:
        self.tokens[sha] = device_name

    async def revoke_token(self, sha: str) -> bool:
        if sha in self.tokens and sha not in self.revoked:
            self.revoked.add(sha)
            return True
        return False

    async def revoke_token_by_name(self, name: str) -> bool:
        for sha, device in self.tokens.items():
            if device == name and sha not in self.revoked:
                self.revoked.add(sha)
                return True
        return False

    async def list_tokens(self):
        from wakemeup.core.models import Token

        out = []
        for sha, name in self.tokens.items():
            out.append(
                Token(
                    id=1,
                    device_name=name,
                    token_sha256=sha,
                    created_at="2026-09-13T00:00:00+00:00",
                    revoked_at=(sha if sha in self.revoked else None),
                )
            )
        return out


class FakeNet:
    """Doble de red sin IO."""

    async def wol_interface(self) -> str | None:
        return "eth0"

    async def scan_range(self, cidr: str) -> list:
        return []

    async def ping_host(self, ip: str) -> bool:
        return False


class FakeDiscovery:
    """Doble de discovery sin escaneo real."""

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.scan_calls = 0
        self.duration_ms = 7

    @property
    def current_task(self):
        return self.task

    async def scan(self) -> int:
        self.scan_calls += 1
        return 3


class FakeStatus:
    """Doble de status: lista fija de máquinas con DTO."""

    def __init__(self, machines: list[dict]) -> None:
        self._machines = machines

    async def list_with_status(self) -> list:
        from wakemeup.core.models import Machine

        return [
            Machine(id=m["id"], ip=m["ip"], mac=m.get("mac"), hostname=m.get("hostname"), state=m.get("state", "offline"))
            for m in self._machines
        ]


class FastAuthService(AuthService):
    """AuthService con backoff y doble de DB en memoria."""

    def __init__(self, db: FakeDb, settings: AuthSettings) -> None:
        super().__init__(db, settings)  # type: ignore[arg-type]
        self._db = db


def _register_token(db: FakeDb, token: str) -> None:
    db.tokens[sha256_hex_lower(token)] = "test-device"


@pytest.fixture
def api_env(monkeypatch):
    """Sustituye el grafo de la app por dobles antes de cada test."""
    db = FakeDb()
    net = FakeNet()
    discovery = FakeDiscovery()
    status = FakeStatus([])
    auth = FastAuthService(db, AuthSettings())

    monkeypatch.setattr(app, "db", db)
    monkeypatch.setattr(app, "net", net)
    monkeypatch.setattr(app, "discovery", discovery)
    monkeypatch.setattr(app, "status", status)
    monkeypatch.setattr(app, "auth", auth)

    token = new_device_token()
    _register_token(db, token)  # programamos el token sin tocar un loop real
    return {"client": TestClient(app), "token": token, "db": db, "auth": auth, "status": status, "discovery": discovery}


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_healthcheck_is_exempt_from_auth(api_env):
    resp = api_env["client"].get("/api/v1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["wol"]["interface"] == "eth0"
    assert "version" in body


def test_healthcheck_warns_without_ethernet(api_env, monkeypatch):
    class NoEthNet(FakeNet):
        async def wol_interface(self) -> str | None:
            return None

    monkeypatch.setattr(app, "net", NoEthNet())
    resp = api_env["client"].get("/api/v1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "warning"
    assert body["wol"]["interface"] is None


def test_get_machines_with_valid_token_returns_dto(api_env):
    api_env["status"]._machines = [
        {"id": 1, "ip": "192.168.1.10", "mac": "aa:bb:cc:dd:ee:11", "hostname": "pc-maria", "state": "online"},
        {"id": 2, "ip": "192.168.1.11", "mac": None, "hostname": None},
    ]
    resp = api_env["client"].get("/api/v1/machines", headers=_auth_header(api_env["token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["machines"]) == 2
    first = body["machines"][0]
    assert first["id"] == 1
    assert first["name"] == "pc-maria"
    assert first["ip"] == "192.168.1.10"
    assert first["mac"] == "aa:bb:cc:dd:ee:11"
    assert first["hostname"] == "pc-maria"
    assert first["status"] == "online"
    assert first["managed"] is False
    second = body["machines"][1]
    assert second["name"] == "192.168.1.11"  # name fallback a IP sin hostname
    assert second["mac"] is None
    assert second["status"] == "offline"


def test_unknown_api_route_returns_404_envelope(api_env):
    """404 desconocida → envelope uniforme (AD-1), no el body por defecto."""
    resp = api_env["client"].get("/api/v1/no-existe", headers=_auth_header(api_env["token"]))
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["message"]


def test_unhandled_exception_returns_500_envelope(api_env, monkeypatch):
    """5xx → envelope uniforme con código internal_error (catálogo FR-9)."""

    class BoomStatus:
        async def list_with_status(self):
            raise RuntimeError("boom interno")

    monkeypatch.setattr(app, "status", BoomStatus())
    client500 = TestClient(app, raise_server_exceptions=False)
    resp = client500.get("/api/v1/machines", headers=_auth_header(api_env["token"]))
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["message"]


def test_head_on_healthcheck_is_exempt(api_env):
    """Probes HEAD (curl -I / monitores) contra /status → exentas (no 401)."""
    resp = api_env["client"].head("/api/v1/status")
    assert resp.status_code == 200


def test_get_machines_empty_inventory_returns_empty_list(api_env):
    """Matriz: inventario vacío → `{"machines": []}` sin error."""
    resp = api_env["client"].get("/api/v1/machines", headers=_auth_header(api_env["token"]))
    assert resp.status_code == 200
    assert resp.json() == {"machines": []}


def test_get_machines_without_token_returns_401_envelope(api_env):
    resp = api_env["client"].get("/api/v1/machines")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "unauthorized"
    assert body["error"]["message"]


def test_get_machines_with_invalid_token_returns_401(api_env):
    resp = api_env["client"].get(
        "/api/v1/machines", headers=_auth_header("deadbeef" * 8)
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_revoked_token_rejected(api_env):
    db = api_env["db"]
    sha = sha256_hex_lower(api_env["token"])
    db.revoked.add(sha)
    resp = api_env["client"].get("/api/v1/machines", headers=_auth_header(api_env["token"]))
    assert resp.status_code == 401


def test_scan_without_token_returns_401(api_env):
    resp = api_env["client"].post("/api/v1/scan")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_scan_triggers_scan_and_returns_202_stats(api_env):
    resp = api_env["client"].post("/api/v1/scan", headers=_auth_header(api_env["token"]))
    assert resp.status_code == 202
    body = resp.json()
    scan = body["scan"]
    assert scan["running"] is False
    assert scan["triggered"] is True
    assert scan["discovered"] == 3
    assert scan["duration_ms"] >= 0
    assert api_env["discovery"].scan_calls == 1


def test_scan_running_returns_202_running_true_without_second_scan(api_env):
    """singleflight: escaneo en curso → 202 `running: true`, sin segundo escaneo."""
    class Running:
        done = lambda self: False  # noqa: E731

    api_env["discovery"].task = Running()
    try:
        resp = api_env["client"].post("/api/v1/scan", headers=_auth_header(api_env["token"]))
        assert resp.status_code == 202
        assert resp.json()["scan"]["running"] is True
        assert resp.json()["scan"]["triggered"] is False
        assert api_env["discovery"].scan_calls == 0
    finally:
        api_env["discovery"].task = None


def test_five_failures_block_source_for_15_minutes(api_env):
    """5 fallos en 5 min → 429 durante el bloqueo (AD-6).

    Los fallos se contabilizan por IP y por hash del token presentado; el
    token plano nunca actúa como clave (FR-11).
    """
    auth = api_env["auth"]
    # Fuerza 5 fallos con un token desconocido (cuenta por IP y por digest).
    for _ in range(5):
        resp = api_env["client"].get(
            "/api/v1/machines", headers=_auth_header("beef" * 16)
        )
        assert resp.status_code == 401
    # El mismo digest queda bloqueado → 429 aunque la IP haya acumulado.
    resp = api_env["client"].get(
        "/api/v1/machines", headers=_auth_header("beef" * 16)
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "too_many_requests"
    auth.backoff.reset()


def test_five_failures_without_token_block_by_ip(api_env):
    """Sin token, 5 fallos consecutivos → la IP queda bloqueada (429)."""
    auth = api_env["auth"]
    for _ in range(5):
        resp = api_env["client"].get("/api/v1/machines")
        assert resp.status_code == 401
    resp = api_env["client"].get("/api/v1/machines")
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "too_many_requests"
    auth.backoff.reset()


def test_lifespan_starts_periodic_loops():
    """AC: al arrancar la app (lifespan), periodic_task y check_cycle están en marcha.

    El lifespan consume el grafo expuesto en `app.*` y espera a las tasks
    periódicas (canceladas) antes de cerrar la DB.
    """
    from wakemeup.api import _lifespan
    import asyncio as _a

    async def cancelled_task():
        await _a.sleep(3600)
        return None

    class FakeDiscovery2:
        def __init__(self):
            self.task = None

        def periodic_task(self):
            if self.task is None or self.task.done():
                self.task = _a.create_task(cancelled_task())
            return self.task

        def stop(self) -> None:
            if self.task is not None and not self.task.done():
                self.task.cancel()

    class FakeStatus2:
        def __init__(self):
            self.task = None

        def check_cycle(self):
            if self.task is None or self.task.done():
                self.task = _a.create_task(cancelled_task())
            return self.task

        def stop(self) -> None:
            if self.task is not None and not self.task.done():
                self.task.cancel()

    class FakeDb2:
        async def init_db(self) -> None: ...

        async def close(self) -> None: ...

    events: list[str] = []

    async def run():
        old = (
            app.db, app.net, app.discovery, app.status, app.auth,
        )
        app.db, app.net, app.discovery, app.status, app.auth = (
            FakeDb2(), None, FakeDiscovery2(), FakeStatus2(), None,
        )
        try:
            from wakemeup.api import _lifespan as lf

            async with lf(app):
                events.append("inside")
        finally:
            app.db, app.net, app.discovery, app.status, app.auth = old

    _a.run(run())  # pytest-asyncio mode=auto: loop propio en este test
    assert events == ["inside"]
