"""Runner de uvicorn que respeta `[api] bind_hosts`/`port` (story 4.1, FR-9/AD-6).

uvicorn por defecto bindea un único host; el despliegue necesita escuchar a la
vez en la tailnet (`100.x`) y en loopback, y tolerar que la interfaz tailnet
aparezca DESPUÉS de que arranque el servicio (`After=tailscaled.service` no
garantiza que la IP esté asignada). `bind_sockets` resuelve ambos requisitos:

- `resolve_bind_hosts` normaliza/deduplica los hosts configurados (vacíos o
  malformados fuera).
- `bind_sockets` intenta bindear cada host con un socket propio y
  `SO_REUSEADDR`; un host no asignable se reintenta hasta `bind_retry_seconds`
  y, agotado el plazo, se omite con warning. Si al menos un host queda
  bindeado, se arranca con los que sí existen (nunca `exit` por una interfaz
  ausente: evita el crash-loop con `Restart=always`). Si ninguno bindea, se
  propaga el error.
- `main` construye `uvicorn.Config` (sin host/port, para no crear un socket
  extra) y ejecuta `uvicorn.Server.run(sockets=…)`.

Nunca `0.0.0.0` ni la LAN física: el operador configura `bind_hosts` y el
instalador detecta la IP tailnet (FR-9/AD-6/NFR-1).
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import sys
import time

import uvicorn

from wakemeup.config import Settings

logger = logging.getLogger(__name__)

# Retardo entre reintentos del bind de un host aún no asignable (tailnet tardía).
_RETRY_INTERVAL_SECONDS = 0.5


def resolve_bind_hosts(hosts: list[str]) -> list[str]:
    """Normaliza/deduplica los hosts del bind, preservando el orden.

    Un host vacío o no parseable se descarta con warning (nunca aborta el
    arranque: el resto se bindea). Las direcciones IPv4-mapped se normalizan a
    su forma IPv4; IPv6 conserva su representación canónica.
    """
    resolved: list[str] = []
    seen: set[str] = set()
    for raw in hosts:
        candidate = (raw or "").strip()
        if not candidate:
            continue
        try:
            addr = ipaddress.ip_address(candidate)
        except ValueError:
            # Un hostname no es una interfaz local: se descarta (el bind real
            # solo acepta direcciones locales; el Host header se cubre aparte
            # con `extra_allowed_hosts`).
            logger.warning("bind host inválido descartado: %r", raw)
            continue
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        normalized = str(addr)
        if normalized in seen:
            continue
        seen.add(normalized)
        resolved.append(normalized)
    return resolved


def _try_bind_one(host: str, port: int) -> socket.socket | None:
    """Intenta bindear `host:port`; `None` si la dirección no es asignable.

    `SO_REUSEADDR` permite re-arrancar sin esperar TIME_WAIT. Un fallo del
    bind (la interfaz tailnet aún no existe → `EADDRNOTAVAIL`) no es fatal: el
    llamador decide reintentar/omitir.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        logger.warning("bind fallido en %s:%s: %s", host, port, exc)
        sock.close()
        return None
    # `listen()` lo hará `loop.create_server(sock=…)` de uvicorn (patrón de la
    # integración gunicorn: el socket llega bindeado, no escuchando).
    sock.set_inheritable(True)
    return sock


def bind_sockets(
    hosts: list[str],
    port: int,
    *,
    retry_seconds: float = 30.0,
    retry_interval: float = _RETRY_INTERVAL_SECONDS,
    sleep=time.sleep,
) -> list[socket.socket]:
    """Bindea `hosts:port` con reintento acotado y degradación no fatal.

    Cada host recibe su propio socket. Los que fallan se reintentan cada
    `retry_interval` hasta agotar `retry_seconds`; entonces se omiten con
    warning. Si ninguno queda bindeado, se lanza `OSError` (el servicio debe
    saber que no escucha en ningún sitio). `sleep` es inyectable en tests.
    """
    resolved = resolve_bind_hosts(hosts)
    if not resolved:
        raise OSError("no hay hosts válidos para el bind ([api] bind_hosts)")

    pending = list(resolved)
    bound: list[socket.socket] = []
    deadline = time.monotonic() + max(retry_seconds, 0.0)

    while pending:
        still: list[str] = []
        for host in pending:
            sock = _try_bind_one(host, port)
            if sock is not None:
                bound.append(sock)
                logger.info("bind correcto en %s:%s", host, port)
            else:
                still.append(host)
        pending = still
        if not pending:
            break
        if time.monotonic() >= deadline:
            for host in pending:
                logger.warning(
                    "host no asignable tras %ss, se omite del bind: %s:%s",
                    retry_seconds,
                    host,
                    port,
                )
            break
        sleep(retry_interval)

    if not bound:
        raise OSError(
            f"ningún bind_hosts asignable en el puerto {port}: {resolved}"
        )
    return bound


def main() -> None:
    """Arranca el runner leyendo `Settings` ([api] bind_hosts/port)."""
    logging.basicConfig(level=logging.INFO)
    settings = Settings()
    hosts = settings.api.bind_hosts
    port = settings.api.port
    try:
        sockets = bind_sockets(
            hosts, port, retry_seconds=settings.api.bind_retry_seconds
        )
    except OSError as exc:
        logger.error("no se pudo bindear ningún host: %s", exc)
        sys.exit(1)

    bound = ", ".join(f"{s.getsockname()[0]}:{s.getsockname()[1]}" for s in sockets)
    logger.info("wakemeup-server escuchando en %s", bound)

    config = uvicorn.Config(
        "wakemeup.api:app",
        # Sin host/port: los sockets ya están bindeados y se pasan a `run`.
        log_level="info",
    )
    server = uvicorn.Server(config)
    server.run(sockets=sockets)


if __name__ == "__main__":
    main()
