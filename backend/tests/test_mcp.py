"""Tests del servidor MCP (stories 3.1 y 3.2, AD-12).

Cubren la matriz backend: descubrimiento de exactamente las 5 tools (sin alta
ni gestión de claves), que las tools envuelven los servicios existentes, el
origen/canal `mcp` de las de control, y la auth+guard solo-tailnet (token MCP
dedicado, token de dispositivo rechazado, IP de LAN física rechazada, revocado).
"""
from __future__ import annotations

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from wakemeup.config import AuthSettings
from wakemeup.core.models import Machine
from wakemeup.services.auth import AuthBackoff, new_device_token, sha256_hex_lower

TOOL_NAMES = {
    "list_machines",
    "get_machine_status",
    "wake_machine",
    "shutdown_machine",
    "force_scan",
}


class _FakeDb:
    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}

    async def init_db(self) -> None: ...
    async def close(self) -> None: ...
    async def begin(self) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...

    async def token_exists(self, digest: str, kind: str = "device") -> bool:
        return self.tokens.get(digest) == kind


class _FakeAuth:
    """Espejo del AuthService real: valida por kind y aplica backoff (AD-6)."""

    def __init__(self, db: _FakeDb) -> None:
        self._db = db
        self.backoff = AuthBackoff(AuthSettings())

    async def authenticate(self, token, ip, kind="device") -> bool:
        digest = sha256_hex_lower(token) if token is not None else None
        if digest is None:
            self.backoff.record_failure(ip)
            return False
        if not await self._db.token_exists(digest, kind):
            self.backoff.record_failure(ip)
            self.backoff.record_failure(digest)
            return False
        self.backoff.success(ip)
        self.backoff.success(digest)
        return True


class _FakeStatus:
    """Lista máquinas fijas (incluida una gestionada)."""

    def __init__(self) -> None:
        self.machines = [
            Machine(id=1, ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10", hostname="pc",
                    state="offline", fingerprint="SHA256:abcd", remote_user="maria",
                    last_origin="mcp", last_change_at="2026-09-15T00:00:00+00:00"),
            Machine(id=2, ip="192.168.1.11", mac=None, hostname=None, state="offline"),
        ]
        self.task: asyncio.Task | None = None

    async def list_with_status(self):
        return list(self.machines)

    def check_cycle(self):
        async def _c():
            await asyncio.sleep(3600)
        self.task = asyncio.get_event_loop().create_task(_c())
        return self.task

    def stop(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()


class _FakeWake:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def wake(self, machine_id, channel="api", token="-"):
        self.calls.append((machine_id, channel, token))
        return {"ok": True}


class _FakeShutdown:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def shutdown(self, machine_id, channel="api", token="-"):
        self.calls.append((machine_id, channel, token))
        return {"ok": True}


class _FakeDiscovery:
    def __init__(self) -> None:
        self.current_task = None
        self.scan_origins: list[str] = []
        self.task: asyncio.Task | None = None

    def periodic_task(self):
        async def _c():
            await asyncio.sleep(3600)
        self.task = asyncio.get_event_loop().create_task(_c())
        return self.task

    def stop(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()

    async def scan(self, origin="scan") -> int:
        self.scan_origins.append(origin)
        return 5


class _FakeEvents:
    def __init__(self) -> None:
        self.published: list = []

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def publish(self, event) -> None:
        self.published.append(event)


class _FakeActivity:
    """Registra entradas de actividad en memoria (FR-11)."""

    def __init__(self) -> None:
        self.entries: list[tuple] = []

    async def record(self, channel, token, machine_id, machine_ip, operation, result) -> None:
        self.entries.append((channel, operation, result))


class _Env:
    def __init__(self) -> None:
        self.db = _FakeDb()
        self.auth = _FakeAuth(self.db)
        self.status = _FakeStatus()
        self.wake = _FakeWake()
        self.shutdown = _FakeShutdown()
        self.discovery = _FakeDiscovery()
        self.events = _FakeEvents()
        self.activity = _FakeActivity()
        self.services = {
            "db": self.db, "net": None, "discovery": self.discovery,
            "status": self.status, "auth": self.auth, "enrollment": None,
            "wake": self.wake, "shutdown": self.shutdown, "activity": self.activity,
            "events": self.events,
        }
        self.mcp_token = new_device_token()
        self.device_token = new_device_token()
        self.db.tokens[sha256_hex_lower(self.mcp_token)] = "mcp"
        self.db.tokens[sha256_hex_lower(self.device_token)] = "device"


def _make_app(env: _Env):
    from mcp.server.transport_security import TransportSecuritySettings

    from wakemeup.api import create_app

    return create_app(
        env.services,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


@pytest.fixture
def env():
    e = _Env()
    e.app = _make_app(e)
    return e


# --- 3.1: descubrimiento y tools ---


@pytest.mark.asyncio
async def test_discovers_exactly_the_five_tools(env):
    tools = await env.app.mcp.list_tools()
    names = {t.name for t in tools}
    assert names == TOOL_NAMES
    # Ninguna tool de alta ni de gestión de claves.
    assert not any("enroll" in n or "key" in n or "token" in n for n in names)


@pytest.mark.asyncio
async def test_list_machines_wraps_status_service(env):
    result = await env.app.mcp.call_tool("list_machines", {})
    payload = result.structured_content["result"]
    assert [m["id"] for m in payload] == [1, 2]
    assert payload[0]["name"] == "pc"
    assert payload[0]["managed"] is True
    assert payload[0]["last_origin"] == "mcp"


@pytest.mark.asyncio
async def test_get_machine_status_by_id(env):
    result = await env.app.mcp.call_tool("get_machine_status", {"machine_id": 2})
    payload = result.structured_content
    assert payload["id"] == 2
    assert payload["status"] == "offline"


@pytest.mark.asyncio
async def test_get_machine_status_unknown_raises_tool_error(env):
    from mcp.server.mcpserver.exceptions import ToolError

    with pytest.raises(ToolError):
        await env.app.mcp.call_tool("get_machine_status", {"machine_id": 999})


@pytest.mark.asyncio
async def test_wake_machine_passes_mcp_channel(env):
    result = await env.app.mcp.call_tool("wake_machine", {"machine_id": 1})
    assert result.structured_content == {"ok": True}
    assert env.wake.calls == [(1, "mcp", "-")]


@pytest.mark.asyncio
async def test_shutdown_machine_passes_mcp_channel(env):
    result = await env.app.mcp.call_tool("shutdown_machine", {"machine_id": 1})
    assert result.structured_content == {"ok": True}
    assert env.shutdown.calls == [(1, "mcp", "-")]


@pytest.mark.asyncio
async def test_force_scan_propagates_mcp_origin(env):
    result = await env.app.mcp.call_tool("force_scan", {})
    assert result.structured_content["discovered"] == 5
    assert env.discovery.scan_origins == ["mcp"]


@pytest.mark.asyncio
async def test_force_scan_result_includes_duration_ms(env):
    """3.1: `force_scan` incluye `duration_ms` como `POST /scan`."""
    result = await env.app.mcp.call_tool("force_scan", {})
    duration = result.structured_content["duration_ms"]
    assert isinstance(duration, int)
    assert duration >= 0


@pytest.mark.asyncio
async def test_force_scan_running_does_not_trigger_second_scan(env):
    class Running:
        done = lambda self: False  # noqa: E731

    env.discovery.current_task = Running()
    result = await env.app.mcp.call_tool("force_scan", {})
    assert result.structured_content == {
        "running": True, "triggered": False, "discovered": None, "duration_ms": None
    }
    assert env.discovery.scan_origins == []


# --- 3.1/3.2: auth y guard en el wire ---


def _initialize(client, token=None):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        },
    }
    return client.post("/api/v1/mcp/", json=body, headers=headers)


def test_mcp_accepts_mcp_token_from_loopback(env):
    with TestClient(env.app, client=("127.0.0.1", 5000)) as client:
        resp = _initialize(client, env.mcp_token)
    assert resp.status_code == 200
    assert "result" in resp.text


def test_mcp_rejects_no_token(env):
    with TestClient(env.app, client=("127.0.0.1", 5000)) as client:
        resp = _initialize(client, None)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_mcp_rejects_device_token(env):
    """Un token de dispositivo no abre el MCP (FR-10b)."""
    with TestClient(env.app, client=("127.0.0.1", 5000)) as client:
        resp = _initialize(client, env.device_token)
    assert resp.status_code == 401


def test_mcp_rejects_unknown_token(env):
    with TestClient(env.app, client=("127.0.0.1", 5000)) as client:
        resp = _initialize(client, "deadbeef" * 8)
    assert resp.status_code == 401


def test_mcp_rejects_revoked_token(env):
    env.db.tokens.pop(sha256_hex_lower(env.mcp_token))
    with TestClient(env.app, client=("127.0.0.1", 5000)) as client:
        resp = _initialize(client, env.mcp_token)
    assert resp.status_code == 401
    # El fallo de auth queda registrado con canal mcp (FR-11).
    assert ("mcp", "mcp_auth", "invalid_token") in env.activity.entries


def test_mcp_rejects_physical_lan_client_without_persisting(env):
    """Desde la LAN física (no loopback/tailnet) la sesión se rechaza (NFR-1).

    El rechazo solo se loguea: persiste un cliente NO autenticado sin backoff,
    así que escribir en `activity_log` permitiría filas ilimitadas.
    """
    with TestClient(env.app, client=("192.168.1.50", 5000)) as client:
        resp = _initialize(client, env.mcp_token)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
    assert env.activity.entries == []


def test_mcp_accepts_tailnet_client(env):
    """Una IP de la tailnet (`100.64.0.0/10`) sí puede abrir el MCP (AD-12)."""
    with TestClient(env.app, client=("100.100.100.5", 5000)) as client:
        resp = _initialize(client, env.mcp_token)
    assert resp.status_code == 200


def test_mcp_routes_are_exempt_from_device_middleware(env):
    """El MCP no exige token de dispositivo: con token MCP y sin él → su propio código."""
    # Sin token: 401 del guard MCP (no el 401 de dispositivo con otro mensaje).
    with TestClient(env.app, client=("127.0.0.1", 5000)) as client:
        resp = _initialize(client, None)
    assert "MCP" in resp.json()["error"]["message"]


def test_mcp_auth_backoff_returns_429_after_repeated_failures(env):
    """3.2 (AD-6): 5 fallos de auth MCP → 429 en la ruta MCP."""
    bad = "beef" * 16
    with TestClient(env.app, client=("127.0.0.1", 5000)) as client:
        for _ in range(5):
            assert _initialize(client, bad).status_code == 401
        resp = _initialize(client, bad)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "too_many_requests"
    env.auth.backoff.reset()


def test_revoking_mcp_token_leaves_device_tokens_untouched(env):
    """Revocar el token MCP no afecta a los de dispositivo (FR-10b)."""
    env.db.tokens.pop(sha256_hex_lower(env.mcp_token))
    with TestClient(env.app, client=("127.0.0.1", 5000)) as client:
        assert _initialize(client, env.mcp_token).status_code == 401
        # El de dispositivo sigue válido en REST.
        resp = client.get(
            "/api/v1/machines",
            headers={"Authorization": f"Bearer {env.device_token}"},
        )
    assert resp.status_code == 200
