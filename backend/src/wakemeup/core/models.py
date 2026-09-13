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


@dataclass(frozen=True)
class MachineDTO:
    """Contrato wire de la API (AD-10): `GET /api/v1/machines`."""

    id: int
    name: str
    ip: str
    mac: str | None
    hostname: str | None
    status: str
    managed: bool


@dataclass(frozen=True)
class Token:
    """Token de dispositivo registrado (tabla `tokens`). Solo guarda el hash."""

    id: int
    device_name: str
    token_sha256: str
    created_at: str
    revoked_at: str | None = None
