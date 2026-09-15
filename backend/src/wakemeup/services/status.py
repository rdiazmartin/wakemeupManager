"""Servicio de estado online/offline (FR-2, AD-3).

El estado de cada máquina del inventario se comprueba con ping (o ARP) por la
LAN física — nunca desde la tailnet — y se persiste con su marca temporal. La
consulta aplica el TTL: un estado sin refrescar más de `ttl_seconds` se reporta
como offline (nunca online caducado).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from wakemeup.adapters.db import Db
from wakemeup.adapters.net import Net
from wakemeup.config import ScanSettings
from wakemeup.core.models import Event, Machine
from wakemeup.services.events import EventBus

logger = logging.getLogger(__name__)

ORIGIN_PERIODIC = "periodic"
ORIGIN_MCP = "mcp"


class StatusService:
    """Comprobación periódica (cada TTL/2) y consulta de estado con TTL."""

    def __init__(
        self, net: Net, db: Db, scan: ScanSettings, events: EventBus | None = None
    ) -> None:
        self._net = net
        self._db = db
        self._scan = scan
        self._events = events
        self._periodic: asyncio.Task | None = None
        self._running = False

    async def check_all(self) -> int:
        """Comprueba el estado de todas las máquinas del inventario por ping.

        Persiste `online`/`offline` con `status_checked_at` en una transacción.
        El barrido de pings es concurrente (semáforo del adaptador net, como
        `scan_range`); la transacción se abre solo para las escrituras, nunca
        alrededor de los pings. Cualquier fallo (incluida cancelación) revierte
        el lote y deja la conexión limpia.

        Epic 3 (AD-11): cada transición real de estado (previo != nuevo) se
        registra en FR-11 y se emite al bus; normalmente con origen `periodic`.
        Excepción (FR-18): si la transición es el desenlace de una acción del
        agente (`last_origin == 'mcp'` con `last_change_at` reciente), se
        CONSERVA el origen `mcp` para que el fallback de polling de la app
        notifique. Si el estado no cambia no se publica nada. Las filas
        `no_fiable` no se tocan (guard de `set_status`) ni transicionan.
        """
        machines = await self._db.list_machines()
        if not machines:
            return 0
        outcomes = await asyncio.gather(
            *(self._net.ping_host(m.ip) for m in machines), return_exceptions=True
        )
        transitions: list[tuple[Machine, str, str]] = []
        await self._db.begin()
        try:
            for machine, outcome in zip(machines, outcomes):
                if isinstance(outcome, BaseException):
                    raise outcome
                new_state = "online" if outcome else "offline"
                if machine.state != "no_fiable" and machine.state != new_state:
                    origin = self._transition_origin(machine)
                    updated = await self._db.set_status(machine.ip, new_state, origin=origin)
                    # Otra escritura (p. ej. un shutdown con mismatch de
                    # fingerprint) pudo marcar `no_fiable` entre la lectura y el
                    # UPDATE: con 0 filas la transición no se persistió y no
                    # debe emitirse actividad/evento espurios.
                    if not updated:
                        logger.info(
                            "%s cambió durante el barrido; transición a %s descartada",
                            machine.ip, new_state,
                        )
                        continue
                    transitions.append((machine, new_state, origin))
                    await self._db.record_activity(
                        origin, "-", machine.id, machine.ip, "status", new_state
                    )
                else:
                    await self._db.set_status(machine.ip, new_state, origin=ORIGIN_PERIODIC)
            await self._db.commit()
        except BaseException:
            await self._db.rollback()
            raise
        self._publish_transitions(transitions)
        return len(machines)

    def _transition_origin(self, machine: Machine) -> str:
        """Origen a atribuir a una transición detectada por el barrido.

        Conserva `mcp` si el último cambio lo hizo el agente y es reciente
        (ventana de 2× el intervalo de estado, mínimo 120 s): es la transición
        resultante de su acción, y el polling de la app debe verlo como `mcp`
        (FR-18). En cualquier otro caso → `periodic`.
        """
        if machine.last_origin == ORIGIN_MCP and self._is_recent_change(machine.last_change_at):
            return ORIGIN_MCP
        return ORIGIN_PERIODIC

    def _is_recent_change(self, changed_at: str | None) -> bool:
        """¿La marca de último cambio está dentro de la ventana del agente?"""
        if changed_at is None:
            return False
        try:
            stamp = datetime.fromisoformat(changed_at)
        except ValueError:
            return False
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        interval = max(self._scan.ttl_seconds // 2, 1)
        window = max(2 * interval, 120)
        return stamp >= datetime.now(timezone.utc) - timedelta(seconds=window)

    def _publish_transitions(self, transitions: list[tuple[Machine, str, str]]) -> None:
        """Emite al bus las transiciones persistidas (nunca bloquea, AD-11)."""
        if self._events is None:
            return
        stamp = datetime.now(timezone.utc).isoformat()
        for machine, new_state, origin in transitions:
            self._events.publish(
                Event(
                    type=f"machine_{new_state}",
                    machine=machine.hostname or machine.ip,
                    timestamp=stamp,
                    origin=origin,
                    machine_id=machine.id,
                    machine_ip=machine.ip,
                )
            )

    async def get_machine_status(self, ip: str) -> str | None:
        """Estado efectivo de la máquina aplicando el TTL.

        Caducado (marca ausente, ilegible o más vieja que `ttl_seconds`) →
        `offline`; sin inventario → `None`.
        """
        status = await self._db.get_status(ip)
        if status is None:
            return None
        state, checked_at = status
        if state == "online" and not self._is_fresh(checked_at):
            logger.info("estado de %s caducado; se reporta offline", ip)
            return "offline"
        return state

    async def list_with_status(self) -> list[Machine]:
        """Inventario completo con el estado efectivo (TTL aplicado), sin persistir.

        El estado caducado se devuelve como offline en la lista; la derivación
        no destruye la última marca conocida en la BD.
        """
        result: list[Machine] = []
        for machine in await self._db.list_machines():
            if machine.state == "online" and not self._is_fresh(machine.status_checked_at):
                machine = Machine(
                    id=machine.id,
                    ip=machine.ip,
                    mac=machine.mac,
                    hostname=machine.hostname,
                    state="offline",
                    status_checked_at=machine.status_checked_at,
                    fingerprint=machine.fingerprint,
                    remote_user=machine.remote_user,
                    last_origin=machine.last_origin,
                    last_change_at=machine.last_change_at,
                )
            result.append(machine)
        return result

    def check_cycle(self, interval_seconds: int | None = None) -> asyncio.Task:
        """Loop periódico (FR-2): comprueba el estado cada TTL/2 segundos.

        Idempotente: una segunda llamada devuelve la tarea ya en marcha.
        `interval_seconds` permite acelerar el tick en tests (default TTL/2).
        Anti-solapamiento: si una comprobación tarda más que el intervalo, el
        siguiente tick espera a que termine en lugar de lanzar otra en paralelo.
        """
        if self._periodic is not None and not self._periodic.done():
            return self._periodic

        async def _loop() -> None:
            ttl = self._scan.ttl_seconds
            interval = interval_seconds if interval_seconds is not None else max(ttl // 2, 1)
            interval = max(interval, 1)
            logger.info("loop de estado cada %d s (TTL %d s)", interval, ttl)
            while True:
                if not self._running:
                    self._running = True
                    try:
                        checked = await self.check_all()
                        logger.info("estado comprobado: %d máquina(s)", checked)
                    except Exception:
                        logger.exception("comprobación de estado falló; reintento en el siguiente tick")
                    finally:
                        self._running = False
                await asyncio.sleep(interval)

        self._periodic = asyncio.create_task(_loop())
        return self._periodic

    def stop(self) -> None:
        """Cancela el loop periódico (lifespan de la app FastAPI)."""
        if self._periodic is not None and not self._periodic.done():
            self._periodic.cancel()

    def _is_fresh(self, checked_at: str | None) -> bool:
        """¿La marca de comprobación está dentro del TTL?"""
        if checked_at is None:
            return False
        try:
            stamp = datetime.fromisoformat(checked_at)
        except ValueError:
            return False
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp >= datetime.now(timezone.utc) - timedelta(seconds=self._scan.ttl_seconds)
