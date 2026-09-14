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
from wakemeup.core.models import Machine

logger = logging.getLogger(__name__)


class StatusService:
    """Comprobación periódica (cada TTL/2) y consulta de estado con TTL."""

    def __init__(self, net: Net, db: Db, scan: ScanSettings) -> None:
        self._net = net
        self._db = db
        self._scan = scan
        self._periodic: asyncio.Task | None = None
        self._running = False

    async def check_all(self) -> int:
        """Comprueba el estado de todas las máquinas del inventario por ping.

        Persiste `online`/`offline` con `status_checked_at` en una transacción.
        El barrido de pings es concurrente (semáforo del adaptador net, como
        `scan_range`); la transacción se abre solo para las escrituras, nunca
        alrededor de los pings. Cualquier fallo (incluida cancelación) revierte
        el lote y deja la conexión limpia.
        """
        machines = await self._db.list_machines()
        if not machines:
            return 0
        outcomes = await asyncio.gather(
            *(self._net.ping_host(m.ip) for m in machines), return_exceptions=True
        )
        await self._db.begin()
        try:
            for machine, outcome in zip(machines, outcomes):
                if isinstance(outcome, BaseException):
                    raise outcome
                await self._db.set_status(machine.ip, "online" if outcome else "offline")
            await self._db.commit()
        except BaseException:
            await self._db.rollback()
            raise
        return len(machines)

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
