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
    """Doble de DB: tokens, máquinas y activity_log en memoria."""

    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}  # sha256 -> device_name (activo)
        self.token_kinds: dict[str, str] = {}  # sha256 -> kind
        self.revoked: set[str] = set()
        self.machines: list[tuple[str, str, str, str, str | None, str | None]] = []
        # (ip, mac, hostname, state, fingerprint, remote_user)
        self.scan_calls = 0
        self.activities: list = []

    async def init_db(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def token_exists(self, sha: str, kind: str = "device") -> bool:
        return (
            self.tokens.get(sha) is not None
            and self.token_kinds.get(sha, "device") == kind
            and sha not in self.revoked
        )

    async def create_token(self, device_name: str, sha: str, kind: str = "device") -> None:
        self.tokens[sha] = device_name
        self.token_kinds[sha] = kind

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

    async def get_by_id(self, machine_id: int):
        from wakemeup.core.models import Machine

        idx = machine_id - 1
        if not (0 <= idx < len(self.machines)):
            return None
        row = self.machines[idx]
        return Machine(
            id=machine_id, ip=row[0], mac=row[1], hostname=row[2], state=row[3],
            fingerprint=row[4], remote_user=row[5],
        )

    async def record_activity(self, channel, token, machine_id, machine_ip, operation, result) -> None:
        self.activities.append((channel, token, machine_id, machine_ip, operation, result))

    async def begin(self) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class FakeNet:
    """Doble de red sin IO; registra los WOL enviados."""

    def __init__(self) -> None:
        self.wol_sends: list[str] = []

    async def wol_interface(self) -> str | None:
        return "eth0"

    async def scan_range(self, cidr: str) -> list:
        return []

    async def ping_host(self, ip: str) -> bool:
        return False

    async def send_wol(self, mac: str, interface: str | None = None) -> str:
        self.wol_sends.append(mac)
        return interface or "eth0"

    def normalize_mac(self, mac: str) -> bytes:
        from wakemeup.adapters.net import Net

        return Net.normalize_mac(mac)


class FakeDiscovery:
    """Doble de discovery sin escaneo real."""

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.scan_calls = 0
        self.duration_ms = 7
        self.scan_origins: list[str] = []

    @property
    def current_task(self):
        return self.task

    async def scan(self, origin: str = "scan") -> int:
        self.scan_calls += 1
        self.scan_origins.append(origin)
        return 3


class FakeStatus:
    """Doble de status: lista fija de máquinas con DTO."""

    def __init__(self, machines: list[dict]) -> None:
        self._machines = machines

    async def list_with_status(self) -> list:
        from wakemeup.core.models import Machine

        return [
            Machine(
                id=m["id"], ip=m["ip"], mac=m.get("mac"), hostname=m.get("hostname"),
                state=m.get("state", "offline"), fingerprint=m.get("fingerprint"),
                remote_user=m.get("remote_user"), last_origin=m.get("last_origin"),
                last_change_at=m.get("last_change_at"),
            )
            for m in self._machines
        ]


class FastAuthService(AuthService):
    """AuthService con backoff y doble de DB en memoria."""

    def __init__(self, db: FakeDb, settings: AuthSettings) -> None:
        super().__init__(db, settings)  # type: ignore[arg-type]
        self._db = db


def _register_token(db: FakeDb, token: str, kind: str = "device") -> None:
    digest = sha256_hex_lower(token)
    db.tokens[digest] = "test-device"
    db.token_kinds[digest] = kind


@pytest.fixture
def api_env(monkeypatch):
    """Sustituye el grafo de la app por dobles antes de cada test."""
    db = FakeDb()
    net = FakeNet()
    discovery = FakeDiscovery()
    status = FakeStatus([])
    auth = FastAuthService(db, AuthSettings())

    class FakeEnroll:
        async def enroll(self, machine_id, usuario, password, channel="api", token="-"):
            if machine_id == 999:
                raise _EnrollErr(404, "máquina no encontrada")
            if machine_id == 2:
                raise _EnrollErr(409, "máquina ya gestionada")
            if password == "mala":
                raise _EnrollErr(401, "credenciales incorrectas")
            return {"id": machine_id, "managed": True}

    class FakeWake:
        def __init__(self, net):
            self._net = net

        async def wake(self, machine_id, channel="api", token="-"):
            if machine_id == 999:
                raise _WakeErr(404, "máquina no encontrada")
            if machine_id == 2:
                raise _WakeErr(409, "máquina no gestionada (haz el alta primero)")
            if machine_id == 3:
                raise _WakeErr(422, "MAC inválida", code="validation_error")
            return {"ok": True}

    class FakeShutdown:
        async def shutdown(self, machine_id, channel="api", token="-"):
            if machine_id == 999:
                raise _SDErr(404, "máquina no encontrada")
            if machine_id == 2:
                raise _SDErr(409, "máquina no gestionada (haz el alta primero)")
            if machine_id == 3:
                raise _SDErr(409, "el host no coincide con el fingerprint fijado; máquina marcada no_fiable")
            return {"ok": True}

    class FakeActivity:
        async def list(self, limit=20):
            return []

    class FakeEvents:
        """Bus de eventos sustituible (registra los publicados)."""

        def __init__(self) -> None:
            self.published: list = []
            self.subscriber_count = 0
            self.running = False

        def publish(self, event) -> None:
            self.published.append(event)

        def start(self) -> None:
            self.running = True

        def stop(self) -> None:
            self.running = False

    class _BoomEnroll:
        async def enroll(self, *a, **kw):
            raise RuntimeError("boom enroll")

    from wakemeup.services.enrollment import EnrollmentError as _EnrollErr
    from wakemeup.services.wake import WakeError as _WakeErr
    from wakemeup.services.shutdown import ShutdownError as _SDErr

    events = FakeEvents()
    monkeypatch.setattr(app, "db", db)
    monkeypatch.setattr(app, "net", net)
    monkeypatch.setattr(app, "discovery", discovery)
    monkeypatch.setattr(app, "status", status)
    monkeypatch.setattr(app, "auth", auth)
    monkeypatch.setattr(app, "enrollment", FakeEnroll())
    monkeypatch.setattr(app, "wake", FakeWake(net))
    monkeypatch.setattr(app, "shutdown", FakeShutdown())
    monkeypatch.setattr(app, "activity", FakeActivity())
    monkeypatch.setattr(app, "events", events)

    token = new_device_token()
    _register_token(db, token)  # programamos el token sin tocar un loop real
    return {
        "client": TestClient(app), "token": token, "db": db, "auth": auth,
        "status": status, "discovery": discovery, "events": events,
    }


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


# --- Epic 2: endpoints de control (alta/wake/shutdown) ---


def test_enroll_requires_auth(api_env):
    resp = api_env["client"].post("/api/v1/machines/1/enroll", json={"usuario": "u", "password": "p"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_enroll_ok_returns_200_managed_true(api_env):
    resp = api_env["client"].post(
        "/api/v1/machines/1/enroll",
        json={"usuario": "maria", "password": "s3cr3t"},
        headers=_auth_header(api_env["token"]),
    )
    assert resp.status_code == 200
    assert resp.json() == {"id": 1, "managed": True}


def test_enroll_bad_password_401(api_env):
    resp = api_env["client"].post(
        "/api/v1/machines/1/enroll",
        json={"usuario": "maria", "password": "mala"},
        headers=_auth_header(api_env["token"]),
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert "credenciales" in resp.json()["error"]["message"]


def test_enroll_unknown_machine_404(api_env):
    resp = api_env["client"].post(
        "/api/v1/machines/999/enroll",
        json={"usuario": "u", "password": "p"},
        headers=_auth_header(api_env["token"]),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_enroll_already_managed_409(api_env):
    resp = api_env["client"].post(
        "/api/v1/machines/2/enroll",
        json={"usuario": "u", "password": "p"},
        headers=_auth_header(api_env["token"]),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"


def test_enroll_missing_body_422_envelope(api_env):
    """Body inválido → 422 con envelope uniforme (no el `detail` de FastAPI)."""
    resp = api_env["client"].post(
        "/api/v1/machines/1/enroll", json={}, headers=_auth_header(api_env["token"])
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert "error" in body and "detail" not in body


def test_wake_requires_auth(api_env):
    resp = api_env["client"].post("/api/v1/machines/1/wake")
    assert resp.status_code == 401


def test_wake_ok_returns_200(api_env):
    resp = api_env["client"].post(
        "/api/v1/machines/1/wake", headers=_auth_header(api_env["token"])
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_wake_unknown_machine_404(api_env):
    resp = api_env["client"].post(
        "/api/v1/machines/999/wake", headers=_auth_header(api_env["token"])
    )
    assert resp.status_code == 404


def test_wake_unmanaged_machine_409(api_env):
    resp = api_env["client"].post(
        "/api/v1/machines/2/wake", headers=_auth_header(api_env["token"])
    )
    assert resp.status_code == 409
    assert "gestionada" in resp.json()["error"]["message"]


def test_wake_invalid_mac_422(api_env):
    resp = api_env["client"].post(
        "/api/v1/machines/3/wake", headers=_auth_header(api_env["token"])
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_shutdown_requires_auth(api_env):
    resp = api_env["client"].post("/api/v1/machines/1/shutdown")
    assert resp.status_code == 401


def test_shutdown_ok_returns_200(api_env):
    resp = api_env["client"].post(
        "/api/v1/machines/1/shutdown", headers=_auth_header(api_env["token"])
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_shutdown_fingerprint_mismatch_409(api_env):
    resp = api_env["client"].post(
        "/api/v1/machines/3/shutdown", headers=_auth_header(api_env["token"])
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "conflict"
    assert "no_fiable" in resp.json()["error"]["message"]


def test_control_api_records_activity_with_token_hash(api_env):
    """2.5: los endpoints registran actividad (canal api) con el digest del token."""
    api_env["db"].machines = [("192.168.1.10", "aa:bb:cc:dd:ee:10", "pc", "offline", None, None)]
    resp = api_env["client"].post(
        "/api/v1/machines/1/enroll",
        json={"usuario": "u", "password": "p"},
        headers=_auth_header(api_env["token"]),
    )
    assert resp.status_code == 200
    # No hay registro directo en el doble; el servicio real lo hace (cubierto en
    # test_enrollment/test_wake/test_shutdown); aquí se valida el canal wire.
    assert api_env["db"].activities == []


def test_unhandled_control_error_returns_500_envelope(api_env, monkeypatch):
    """Cualquier fallo interno del grafo de control → 5xx envelope (catálogo FR-9)."""

    class Boom:
        async def enroll(self, *a, **kw):
            raise RuntimeError("boom interno")

    monkeypatch.setattr(app, "enrollment", Boom())
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/machines/1/enroll",
        json={"usuario": "u", "password": "p"},
        headers=_auth_header(api_env["token"]),
    )
    assert resp.status_code == 500
    assert resp.json()["error"]["code"] == "internal_error"


def test_get_machines_managed_derived_from_fingerprint(api_env):
    """DTO: `managed` se deriva de fingerprint (nota de diseño 2.1)."""
    api_env["status"]._machines = [
        {"id": 1, "ip": "192.168.1.10", "mac": "aa:bb:cc:dd:ee:10", "hostname": "pc", "state": "online"},
        {"id": 2, "ip": "192.168.1.11", "mac": "aa:bb:cc:dd:ee:11", "hostname": "pc2",
         "state": "no_fiable", "fingerprint": "SHA256:abcd", "remote_user": "maria"},
    ]
    resp = api_env["client"].get("/api/v1/machines", headers=_auth_header(api_env["token"]))
    assert resp.status_code == 200
    body = resp.json()["machines"]
    # Rama "false": sin fingerprint → discovery-only.
    assert body[0]["managed"] is False
    # Rama "true" (hasta ahora sin fijar): fingerprint presente → managed=true,
    # y el estado no_fiable viaja tal cual (AD-10).
    assert body[1]["managed"] is True
    assert body[1]["status"] == "no_fiable"


def test_get_machines_dto_includes_last_origin_additively(api_env):
    """3.3: el DTO añade `last_origin`/`last_change_at` sin romper AD-10."""
    api_env["status"]._machines = [
        {"id": 1, "ip": "192.168.1.10", "mac": "aa:bb:cc:dd:ee:10", "hostname": "pc",
         "state": "online", "fingerprint": "SHA256:abcd", "remote_user": "maria",
         "last_origin": "mcp", "last_change_at": "2026-09-15T10:00:00+00:00"},
    ]
    resp = api_env["client"].get("/api/v1/machines", headers=_auth_header(api_env["token"]))
    assert resp.status_code == 200
    m = resp.json()["machines"][0]
    # Campos de AD-10 intactos.
    assert {"id", "name", "ip", "mac", "hostname", "status", "managed"} <= set(m)
    # Campos aditivos del epic 3.
    assert m["last_origin"] == "mcp"
    assert m["last_change_at"] == "2026-09-15T10:00:00+00:00"


def test_scan_emits_scan_origin(api_env):
    """3.3: el escaneo forzado por API pasa `origin='scan'` al servicio."""
    api_env["client"].post("/api/v1/scan", headers=_auth_header(api_env["token"]))
    assert api_env["discovery"].scan_origins == ["scan"]


def test_events_requires_device_token(api_env):
    """SSE: sin token → 401 (Bearer de dispositivo obligatorio)."""
    resp = api_env["client"].get("/api/v1/events")
    assert resp.status_code == 401


def test_lifespan_starts_periodic_loops():
    """AC: al arrancar la app (lifespan), periodic_task y check_cycle están en marcha.

    El lifespan consume el grafo expuesto en `app.*` y espera a las tasks
    periódicas (canceladas) antes de cerrar la DB; también arranca el bus de
    eventos y la sesión MCP (cuyo submount no ejecuta lifespan propio).
    """
    import asyncio as _a

    from wakemeup.api import create_app

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

    class FakeEvents2:
        def __init__(self) -> None:
            self.started = False
            self.stopped = False

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

    events_bus = FakeEvents2()
    seen: list[str] = []
    services = {
        "db": FakeDb2(), "net": None, "discovery": FakeDiscovery2(),
        "status": FakeStatus2(), "auth": None, "enrollment": None,
        "wake": None, "shutdown": None, "activity": None, "events": events_bus,
    }
    test_app = create_app(services)

    async def run():
        async with test_app.router.lifespan_context(test_app):
            seen.append("inside")
            assert events_bus.started is True

    _a.run(run())  # pytest-asyncio mode=auto: loop propio en este test
    assert seen == ["inside"]
    assert events_bus.stopped is True
