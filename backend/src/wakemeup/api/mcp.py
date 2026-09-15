"""Servidor MCP del BE (AD-12, epic 3 / story 3.1).

Expone el SDK oficial `mcp` 2.2 montado sobre la app FastAPI. Las tools de v1
son exactamente `list_machines`, `get_machine_status`, `wake_machine`,
`shutdown_machine` y `force_scan`; NUNCA hay tool de alta ni de gestión de
claves (el agente jamás ve passwords SSH, FR-16).

Las tools NO implementan lógica propia: envuelven los servicios existentes
(`status`, `wake`, `shutdown`, `discovery`) igual que la API (AD-4/AD-12). Las
de control pasan `channel`/`origin` `mcp`, de modo que el registro de actividad
distingue el canal (`api|mcp`, FR-11) y el bus emite el evento con origen `mcp`
(AD-11) para que la app notifique.

El sub-mount no ejecuta su propio lifespan: el host entra
`server.session_manager.run()` en el lifespan de FastAPI (nota de diseño 3.1).
La autenticación/guard se resuelve en la capa FastAPI antes del submount.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from wakemeup.services.activity import CHANNEL_MCP
from wakemeup.services.discovery import ORIGIN_MCP
from wakemeup.services.shutdown import ShutdownError
from wakemeup.services.wake import WakeError

logger = logging.getLogger(__name__)

MCP_PATH = "/api/v1/mcp"

# Nombres EXACTOS de las tools de v1 (FR-16). Cualquier lista distinta
# incumple el contrato: los tests verifican este conjunto.
TOOL_NAMES = (
    "list_machines",
    "get_machine_status",
    "wake_machine",
    "shutdown_machine",
    "force_scan",
)

INSTRUCTIONS = (
    "Servidor MCP de wakemeupManager. Consulta y controla las máquinas de la "
    "red: listar inventario, ver estado, encender (WOL), apagar (SSH) y forzar "
    "un escaneo. No expone el alta de máquinas ni la gestión de claves."
)


class MachineView(BaseModel):
    """Vista de máquina de las tools de lectura (coherente con `GET /machines`)."""

    id: int
    name: str
    ip: str
    mac: str | None
    hostname: str | None
    status: str
    managed: bool
    last_origin: str | None = None
    last_change_at: str | None = None


class ActionResult(BaseModel):
    """Resultado de las tools de control (mismo `{"ok": true}` que la API)."""

    ok: bool


class ScanResult(BaseModel):
    """Resultado de `force_scan` (mismo contrato que `POST /scan`)."""

    running: bool
    triggered: bool
    discovered: int | None = None
    duration_ms: int | None = None


def _machine_view(machine) -> MachineView:
    """DTO de máquina de las tools de lectura (coherente con `GET /machines`)."""
    return MachineView(
        id=machine.id,
        name=machine.hostname or machine.ip,
        ip=machine.ip,
        mac=machine.mac,
        hostname=machine.hostname,
        status=machine.state,
        managed=machine.fingerprint is not None,
        last_origin=machine.last_origin,
        last_change_at=machine.last_change_at,
    )


def create_mcp_server(services: Callable[[], Any]) -> MCPServer:
    """Construye el `MCPServer` con las 5 tools que envuelven los servicios.

    `services` es un proveedor (p. ej. `lambda: app`) que resuelve el grafo de
    servicios en cada llamada: así el MCP usa los mismos objetos que la API y
    los tests pueden sustituirlos (`app.status = ...`) sin reconstruir el
    servidor.
    """
    server = MCPServer(
        name="wakemeupManager",
        instructions=INSTRUCTIONS,
        version="1.0.0",
    )

    @server.tool()
    async def list_machines() -> list[MachineView]:
        """Lista las máquinas del inventario con su estado y si están gestionadas."""
        machines = await services().status.list_with_status()
        return [_machine_view(m) for m in machines]

    @server.tool()
    async def get_machine_status(machine_id: int) -> MachineView:
        """Devuelve el estado efectivo (TTL aplicado) de una máquina por su id."""
        machines = await services().status.list_with_status()
        machine = next((m for m in machines if m.id == machine_id), None)
        if machine is None:
            raise ToolError(f"máquina no encontrada: {machine_id}")
        return _machine_view(machine)

    @server.tool()
    async def wake_machine(machine_id: int) -> ActionResult:
        """Enciende una máquina gestionada por Wake-on-LAN (mismo servicio que la API)."""
        try:
            result = await services().wake.wake(
                machine_id, channel=CHANNEL_MCP, token="-"
            )
        except WakeError as exc:
            raise ToolError(exc.message) from exc
        return ActionResult(**result)

    @server.tool()
    async def shutdown_machine(machine_id: int) -> ActionResult:
        """Apaga una máquina gestionada verificado el fingerprint (mismo servicio que la API)."""
        try:
            result = await services().shutdown.shutdown(
                machine_id, channel=CHANNEL_MCP, token="-"
            )
        except ShutdownError as exc:
            raise ToolError(exc.message) from exc
        return ActionResult(**result)

    @server.tool()
    async def force_scan() -> ScanResult:
        """Fuerza un escaneo de la red; las transiciones quedan con origen `mcp`."""
        discovery = services().discovery
        if discovery.current_task is not None:
            return ScanResult(running=True, triggered=False)
        started = time.monotonic()
        discovered = await discovery.scan(origin=ORIGIN_MCP)
        duration_ms = int((time.monotonic() - started) * 1000)
        return ScanResult(
            running=False, triggered=True, discovered=discovered, duration_ms=duration_ms
        )

    return server
