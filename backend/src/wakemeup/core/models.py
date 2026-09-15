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
    last_origin: str | None = None
    last_change_at: str | None = None


@dataclass(frozen=True)
class MachineDTO:
    """Contrato wire de la API (AD-10): `GET /api/v1/machines`.

    Epic 3 añade de forma ADITIVA `last_origin`/`last_change_at` (decisión del
    usuario: notificación por polling con el stream caído). El resto de campos
    de AD-10 quedan intactos: los consumidores existentes no se rompen.
    """

    id: int
    name: str
    ip: str
    mac: str | None
    hostname: str | None
    status: str
    managed: bool
    last_origin: str | None = None
    last_change_at: str | None = None


@dataclass(frozen=True)
class Token:
    """Token registrado (tabla `tokens`). Solo guarda el hash.

    `kind` discrimina el token de dispositivo (`device`, REST/SSE) del token
    dedicado del MCP (`mcp`, epic 3 / FR-10b): un tipo no abre la superficie
    del otro y revocar uno no afecta al otro.
    """

    id: int
    device_name: str
    token_sha256: str
    created_at: str
    revoked_at: str | None = None
    kind: str = "device"


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


@dataclass(frozen=True)
class Event:
    """Evento de estado del bus (AD-11, epic 3).

    Shape wire del SSE `{type, machine, timestamp, origin}`. `machine` es la
    etiqueta de la máquina afectada (hostname o IP) y `origin` ∈
    {`api`, `mcp`, `scan`, `periodic`}. El evento NUNCA transporta credenciales
    ni claves (FR-11); `machine_id`/`machine_ip` se mantienen solo para uso
    interno (persistencia/resolución del nombre) y no se serializan al stream.
    """

    type: str
    machine: str
    timestamp: str
    origin: str
    machine_id: int | None = None
    machine_ip: str | None = None

    def to_payload(self) -> dict:
        """Serialización wire del evento (sin campos internos)."""
        return {
            "type": self.type,
            "machine": self.machine,
            "timestamp": self.timestamp,
            "origin": self.origin,
        }
