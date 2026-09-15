"""Tests del bus de eventos asyncio y del stream SSE (story 3.3, AD-11).

Cubren la matriz backend de 3.3: pub/sub (varios suscriptores, sin suscriptores
→ se omite, nunca bloquea) y `GET /api/v1/events` (content-type SSE, Bearer de
dispositivo, evento emitido al stream, 401 sin token / con token MCP,
desconexión segura).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from wakemeup.core.models import Event
from wakemeup.services.events import EventBus


def _event(origin: str = "api", type_: str = "machine_online") -> Event:
    return Event(type=type_, machine="pc", timestamp="2026-09-15T00:00:00+00:00", origin=origin)


@pytest.mark.asyncio
async def test_publish_delivers_to_subscriber():
    bus = EventBus()
    async with bus.subscribe() as queue:
        bus.publish(_event())
        received = queue.get_nowait()
    assert received.type == "machine_online"
    assert received.origin == "api"


@pytest.mark.asyncio
async def test_publish_without_subscribers_is_dropped():
    """Sin suscriptores el evento se omite (v1 sin cola); no rompe."""
    bus = EventBus()
    bus.publish(_event())  # no lanza
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_publish_without_subscribers_does_not_queue():
    """Tras entrar y salir un suscriptor no queda entrega pendiente para otro."""
    bus = EventBus()
    async with bus.subscribe():
        pass
    bus.publish(_event())
    async with bus.subscribe() as queue:
        assert queue.qsize() == 0


@pytest.mark.asyncio
async def test_publish_fans_out_to_all_subscribers():
    bus = EventBus()
    async with bus.subscribe() as q1, bus.subscribe() as q2:
        assert bus.subscriber_count == 2
        bus.publish(_event())
        assert q1.get_nowait().origin == "api"
        assert q2.get_nowait().origin == "api"
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_subscribe_unregisters_on_exit():
    bus = EventBus()
    async with bus.subscribe():
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_slow_subscriber_is_dropped_without_blocking_publisher():
    """Un cliente lento no bloquea al publicador: su cola se descarta."""
    bus = EventBus(maxsize=1)
    async with bus.subscribe() as queue:
        bus.publish(_event())  # llena la cola
        bus.publish(_event())  # se descarta, no bloquea
        assert queue.qsize() == 1
    # El publicador nunca lanzó QueueFull hacia arriba.


@pytest.mark.asyncio
async def test_event_payload_excludes_internal_fields():
    """El wire del evento lleva solo {type, machine, timestamp, origin}."""
    payload = _event().to_payload()
    assert set(payload) == {"type", "machine", "timestamp", "origin"}


# --- SSE: `GET /api/v1/events` ---
#
# TestClient/httpx-ASGITransport buffer la respuesta completa, así que no
# valen para un stream sin fin. Un controlador ASGI mínimo permite hablar el
# protocolo directo: enviar la petición, leer `http.response.start`/`body`
# de forma incremental y publicar en el bus en el mismo event loop.


class _FakeDb:
    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}

    async def token_exists(self, digest: str, kind: str = "device") -> bool:
        return self.tokens.get(digest) == kind


class _FakeAuthService:
    """Auth mínima con backoff real y `token_exists` por kind."""

    def __init__(self, db: _FakeDb) -> None:
        from wakemeup.config import AuthSettings
        from wakemeup.services.auth import AuthBackoff

        self._db = db
        self.backoff = AuthBackoff(AuthSettings())

    async def authenticate(self, token, ip, kind="device") -> bool:
        from wakemeup.services.auth import sha256_hex_lower

        if token is None:
            return False
        return await self._db.token_exists(sha256_hex_lower(token), kind)


@pytest.fixture
def sse_app():
    """App con solo los dobles necesarios para el endpoint SSE."""
    from fastapi import FastAPI
    from starlette.applications import Starlette

    from wakemeup.api import _register_routes
    from wakemeup.services.auth import new_device_token, sha256_hex_lower

    db = _FakeDb()
    bus = EventBus()
    test_app = FastAPI()
    test_app.db = db
    test_app.events = bus
    test_app.auth = _FakeAuthService(db)
    # `_register_routes` solo necesita un submount para montar (`Starlette()`).
    _register_routes(test_app, Starlette())

    device_token = new_device_token()
    mcp_token = new_device_token()
    db.tokens[sha256_hex_lower(device_token)] = "device"
    db.tokens[sha256_hex_lower(mcp_token)] = "mcp"
    return {
        "app": test_app, "bus": bus, "device_token": device_token,
        "mcp_token": mcp_token,
    }


class _AsgiResult:
    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.messages: list[dict] = []


async def _run_asgi_asgi(
    app, method: str, path: str, headers: list[tuple[bytes, bytes]],
    client: tuple[str, int] = ("127.0.0.1", 43210),
):
    """Arranca la app ASGI en una task y devuelve `(result, task, receive_q)`."""
    received = asyncio.Queue()
    result = _AsgiResult()

    async def receive():
        return await received.get()

    async def send(message):
        if message["type"] == "http.response.start":
            result.status = message["status"]
            result.headers = {
                k.decode(): v.decode() for k, v in message.get("headers", [])
            }
        result.messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": client,
        "server": ("testserver", 80),
    }
    await received.put({"type": "http.request", "body": b"", "more_body": False})
    task = asyncio.create_task(app(scope, receive, send))
    # Espera a `http.response.start` (la app escribe la cabecera al primer yield).
    for _ in range(100):
        if result.status is not None:
            break
        await asyncio.sleep(0.01)
    return result, task, received


def _body_text(result: _AsgiResult) -> str:
    return b"".join(
        m.get("body", b"") for m in result.messages if m["type"] == "http.response.body"
    ).decode()


async def _drain_disconnect(task, received):
    await received.put({"type": "http.disconnect"})
    try:
        await asyncio.wait_for(task, timeout=2)
    except asyncio.TimeoutError:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_sse_receives_published_event(sse_app):
    """SSE: con token de dispositivo el stream es text/event-stream y recibe el evento."""
    bus = sse_app["bus"]
    token = sse_app["device_token"]
    headers = [
        (b"authorization", f"Bearer {token}".encode()),
        (b"accept", b"text/event-stream"),
    ]
    result, task, received = await _run_asgi_asgi(sse_app["app"], "GET", "/api/v1/events", headers)
    try:
        assert result.status == 200
        assert result.headers["content-type"].startswith("text/event-stream")
        # Anti-buffering: los proxies no deben coalescer el stream.
        assert result.headers["cache-control"] == "no-cache"
        assert result.headers["x-accel-buffering"] == "no"
        assert ": connected" in _body_text(result)
        bus.publish(_event(origin="mcp"))
        for _ in range(100):
            text = _body_text(result)
            if "data: " in text:
                break
            await asyncio.sleep(0.01)
        text = _body_text(result)
        data_line = next(l for l in text.splitlines() if l.startswith("data: "))
        assert json.loads(data_line[len("data: "):]) == {
            "type": "machine_online",
            "machine": "pc",
            "timestamp": "2026-09-15T00:00:00+00:00",
            "origin": "mcp",
        }
    finally:
        await _drain_disconnect(task, received)


@pytest.mark.asyncio
async def test_sse_without_token_returns_401(sse_app):
    result, task, received = await _run_asgi_asgi(sse_app["app"], "GET", "/api/v1/events", [])
    await task
    assert result.status == 401


@pytest.mark.asyncio
async def test_sse_with_mcp_token_returns_401(sse_app):
    """El token MCP no abre el stream de dispositivo (FR-10b)."""
    headers = [(b"authorization", f"Bearer {sse_app['mcp_token']}".encode())]
    result, task, received = await _run_asgi_asgi(sse_app["app"], "GET", "/api/v1/events", headers)
    await task
    assert result.status == 401


@pytest.mark.asyncio
async def test_sse_rejects_untrusted_lan_client(sse_app):
    """AD-6/AD-11: el stream es solo tailnet; cliente de la LAN física → 403."""
    headers = [(b"authorization", f"Bearer {sse_app['device_token']}".encode())]
    result, task, received = await _run_asgi_asgi(
        sse_app["app"], "GET", "/api/v1/events", headers, client=("192.168.1.50", 5000)
    )
    await task
    assert result.status == 403
    assert b"forbidden" in b"".join(
        m.get("body", b"") for m in result.messages if m["type"] == "http.response.body"
    )


@pytest.mark.asyncio
async def test_sse_disconnect_unsubscribes_and_service_survives(sse_app):
    """El suscriptor cae: la suscripción se retira y el servicio sigue."""
    bus = sse_app["bus"]
    token = sse_app["device_token"]
    headers = [(b"authorization", f"Bearer {token}".encode())]
    result, task, received = await _run_asgi_asgi(sse_app["app"], "GET", "/api/v1/events", headers)
    assert result.status == 200
    assert bus.subscriber_count == 1
    await _drain_disconnect(task, received)
    assert bus.subscriber_count == 0
    # El bus sigue operativo aunque el cliente cayera.
    bus.publish(_event())
    assert bus.subscriber_count == 0
