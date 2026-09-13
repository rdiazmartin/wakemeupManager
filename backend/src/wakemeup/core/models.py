"""Núcleo sin IO: entidades del inventario de máquinas (AD-2).

En esta story solo existe el inventario de descubrimiento: identidad efímera
por IP con MAC/hostname opcionales. La identidad `id` + `host_fingerprint` del
AD-2 se completa en el alta (Epic 2).
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class HostInfo:
    """Máquina observada en un escaneo (resultado del adaptador net)."""

    ip: str
    mac: str | None = None
    hostname: str | None = None


@dataclass(frozen=True)
class Machine:
    """Fila del inventario persistido (tabla `machines`)."""

    id: int
    ip: str
    mac: str | None = None
    hostname: str | None = None
    state: str = "offline"
    status_checked_at: str | None = None
