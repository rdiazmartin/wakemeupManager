"""Tests del runner uvicorn multi-host (story 4.1).

Matriz de bind/retry con sockets REALES en loopback: bind tailnet viva
(simulada con varias direcciones de loopback), host no asignable que degrada
sin abortar, resolución/normalización de hosts y arranque efectivo de uvicorn
con los sockets bindeados sirviendo `GET /api/v1/status` 200.
"""
from __future__ import annotations

import socket
import time

import pytest

from wakemeup.server import bind_sockets, resolve_bind_hosts


def test_resolve_bind_hosts_dedup_and_normalize() -> None:
    """Hosts vacíos/duplicados/inválidos se descartan; IPv4-mapped → IPv4."""
    resolved = resolve_bind_hosts(
        ["127.0.0.1", "127.0.0.1", "", "   ", "::ffff:127.0.0.1", "fd7a::1", "no-es-ip"]
    )
    assert resolved == ["127.0.0.1", "fd7a::1"]


def test_resolve_bind_hosts_empty() -> None:
    assert resolve_bind_hosts([]) == []
    assert resolve_bind_hosts(["", "  "]) == []


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_bind_sockets_two_loopback_hosts() -> None:
    """AC: con dos hosts asignables (aquí loopback) se bindean ambos y la API
    podría responder en ambos (se verifica el bind de los dos sockets)."""
    port = _free_port()
    sockets = bind_sockets(["127.0.0.1", "127.0.0.2"], port, retry_seconds=0)
    try:
        assert len(sockets) == 2
        names = {s.getsockname()[0] for s in sockets}
        assert names == {"127.0.0.1", "127.0.0.2"}
        assert {s.getsockname()[1] for s in sockets} == {port}
    finally:
        for sock in sockets:
            sock.close()


def test_bind_sockets_skips_unassignable_with_warning_and_continues(caplog) -> None:
    """Un host no asignable se omite con warning; el resto se bindea (nunca aborta)."""
    port = _free_port()
    with caplog.at_level("WARNING"):
        sockets = bind_sockets(
            ["127.0.0.1", "192.0.2.123"],  # TEST-NET-1: no asignable en el host
            port,
            retry_seconds=0,
        )
    try:
        assert len(sockets) == 1
        assert sockets[0].getsockname()[0] == "127.0.0.1"
        assert "192.0.2.123" in caplog.text
        assert "se omite del bind" in caplog.text
    finally:
        for sock in sockets:
            sock.close()


def test_bind_sockets_retries_until_deadline_then_degrades() -> None:
    """Un host ausente se reintenta mientras corre el plazo y luego degrada."""
    port = _free_port()
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        time.sleep(0.01)  # deja avanzar el reloj real entre reintentos

    sockets = bind_sockets(
        ["127.0.0.1", "192.0.2.123"],
        port,
        retry_seconds=0.5,
        retry_interval=0.05,
        sleep=fake_sleep,
    )
    try:
        assert len(sockets) == 1
        assert sockets[0].getsockname()[0] == "127.0.0.1"
        assert sleeps  # hubo al menos un reintento antes del deadline
    finally:
        for sock in sockets:
            sock.close()


def test_bind_sockets_raises_when_none_bindable() -> None:
    """Si NINGÚN host es asignable, se propaga OSError (el servicio no escucha)."""
    port = _free_port()
    with pytest.raises(OSError):
        bind_sockets(["192.0.2.123"], port, retry_seconds=0)


def test_bind_sockets_no_valid_hosts_raises() -> None:
    with pytest.raises(OSError):
        bind_sockets(["", "invalido"], 12345, retry_seconds=0)


def test_uvicorn_serves_status_on_bound_sockets() -> None:
    """Verificación de contrato: los sockets bindeados sirven `/api/v1/status` 200.

    Ejercita `uvicorn.Server.run(sockets=…)` (el mismo camino que `main`) en un
    hilo, sobre la app real, y consulta el healthcheck exento de auth.
    """
    import threading
    import urllib.request

    import uvicorn

    port = _free_port()
    sockets = bind_sockets(["127.0.0.1"], port, retry_seconds=0)

    config = uvicorn.Config("wakemeup.api:app", log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(
        target=server.run, kwargs={"sockets": sockets}, daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 20
        status = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/v1/status", timeout=1.0
                ) as response:
                    status = response.status
                    break
            except Exception:
                time.sleep(0.1)
        assert status == 200
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        for sock in sockets:
            sock.close()
