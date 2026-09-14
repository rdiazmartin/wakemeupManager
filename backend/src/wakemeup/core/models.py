"""Núcleo sin IO: entidades del inventario de máquinas (AD-2).

Tras el epic 2 la identidad `id` + `fingerprint` + `remote_user` (AD-2/AD-9)
se completa en el alta: `managed` se deriva de `fingerprint IS NOT NULL` y el
estado `no_fiable` (AD-10) se persiste en `machines.state`.
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
    """Fila del inventario persistido (tabla `machines`).

    `fingerprint` y `remote_user` se fijan en el alta (story 2.1): el
    fingerprint es el `SHA256:<base64>` de la host key real (AD-2) y
    `remote_user` el usuario SSH de control.
    """

    id: int
    ip: str
    mac: str | None = None
    hostname: str | None = None
    state: str = "offline"
    status_checked_at: str | None = None
    fingerprint: str | None = None
    remote_user: str | None = None


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


@dataclass(frozen=True)
class ActivityEntry:
    """Entrada del registro de actividad (tabla `activity_log`, story 2.5).

    Shape FR-11: timestamp, canal (`api|mcp`), token, máquina y resultado.
    NUNCA passwords ni el token plano.
    """

    id: int
    timestamp: str
    channel: str
    token: str
    machine_id: int | None
    machine_ip: str | None
    operation: str
    result: str
