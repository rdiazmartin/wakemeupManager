"""Servicio de descubrimiento (FR-1/FR-3 parcial, AD-3).

Singleflight de unión: si un escaneo está en marcha, las llamadas concurrentes
se unen a él (esperan y obtienen su resultado) en lugar de ejecutar un segundo
escaneo (AD-3: un único escaneo a la vez). Excluye las interfaces propias del
BE (incluida tailnet/loopback) y hace upsert no destructivo del inventario:
quien deja de responder permanece en la tabla (offline llega en la story 1.3).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from wakemeup.adapters.db import Db
from wakemeup.adapters.net import Net
from wakemeup.config import ScanSettings
from wakemeup.core.models import Event
from wakemeup.services.activity import ActivityService
from wakemeup.services.events import EventBus

logger = logging.getLogger(__name__)

ORIGIN_SCAN = "scan"
ORIGIN_PERIODIC = "periodic"
ORIGIN_MCP = "mcp"


class DiscoveryService:
    """Orquestación del escaneo: ping+ARP → upsert, con singleflight."""

    def __init__(
        self, net: Net, db: Db, scan: ScanSettings, events: EventBus | None = None,
        activity: ActivityService | None = None,
    ) -> None:
        self._net = net
        self._db = db
        self._scan = scan
        self._events = events
        self._activity = activity
        self._lock = asyncio.Lock()
        self._current: asyncio.Task[int] | None = None
        self._in_flight = False
        self._periodic: asyncio.Task | None = None

    async def scan(self, origin: str = ORIGIN_SCAN) -> int:
        """Ejecuta un escaneo o se une al escaneo en marcha (singleflight, AD-3).

        Devuelve el nº de hosts upserted. Llamadas concurrentes → una sola
        ejecución efectiva: el segundo `scan()` espera y comparte el resultado.

        `origin` (epic 3, AD-11): `scan` para el forzado por API, `mcp` cuando
        lo pidió el agente y `periodic` en el loop. El evento `scan_done` sale
        con ese origen (la transición de estado real del host la emite luego el
        loop de estado con origen `periodic`).
        """
        current = self._current
        if current is not None and not current.done():
            logger.info("escaneo en curso; la llamada concurrente se une (singleflight, AD-3)")
            return await current

        async with self._lock:
            current = self._current
            if current is not None and not current.done():
                logger.info("escaneo en curso; la llamada concurrente se une (singleflight, AD-3)")
                return await current
            current = asyncio.create_task(self._run())
            self._current = current

        result = await current
        # Solo el iniciador del escaneo persiste y emite `scan_done` (con su
        # propio origen); las llamadas que se unen al singleflight no lo duplican.
        await self._record_scan(origin, result)
        self._publish_scan_done(origin, result)
        return result

    async def _run(self) -> int:
        """Escaneo efectivo del rango configurado + upsert del inventario."""
        self._in_flight = True
        try:
            hosts = await self._net.scan_range(self._scan.range)
        finally:
            self._in_flight = False
        await self._db.begin()
        try:
            for host in hosts:
                await self._db.upsert_machine(ip=host.ip, mac=host.mac, hostname=host.hostname)
        except Exception:
            await self._db.rollback()
            raise
        await self._db.commit()
        logger.info("escaneo completado: %d host(s) en %s", len(hosts), self._scan.range)
        return len(hosts)

    async def _record_scan(self, origin: str, discovered: int) -> None:
        """Persiste la acción de escaneo en FR-11 con el canal del origen.

        Cada registro abre/cierra transacción explícita (como los demás
        servicios): un INSERT fuera de transacción deja una implícita abierta y
        el siguiente `db.begin()` del loop de estado fallaría. El canal es el
        valor del origen (`scan`/`mcp`/`periodic`).
        """
        if self._activity is None:
            return
        await self._db.begin()
        try:
            await self._activity.record(
                origin, "-", None, None, "scan", str(discovered)
            )
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise

    def _publish_scan_done(self, origin: str, discovered: int) -> None:
        """Emite `scan_done` con el origen del disparador (AD-11, epic 3)."""
        if self._events is None:
            return
        self._events.publish(
            Event(
                type="scan_done",
                machine="-",
                timestamp=datetime.now(timezone.utc).isoformat(),
                origin=origin,
            )
        )

    @property
    def in_flight(self) -> bool:
        """¿Hay un escaneo corriendo ahora mismo?"""
        return self._in_flight

    @property
    def current_task(self) -> asyncio.Task[int] | None:
        """Tarea del escaneo en curso (estado real, deferred 1.2).

        A diferencia de `in_flight` —que se limpia al terminar el barrido de
        pings, antes del upsert—, esto devuelve la tarea viva mientras el
        escaneo completo no finaliza. El endpoint `POST /scan` lo usa para
        responder 202 `running: true` sin lanzar un segundo escaneo.
        """
        if self._current is not None and not self._current.done():
            return self._current
        return None

    def stop(self) -> None:
        """Cancela el loop periódico (lifespan de la app FastAPI)."""
        if self._periodic is not None and not self._periodic.done():
            self._periodic.cancel()

    def periodic_task(self) -> asyncio.Task:
        """Loop periódico (FR-3): escanea al arrancar y redispara cada interval_seconds.

        Idempotente: una segunda llamada devuelve la tarea ya en marcha en lugar
        de arrancar un segundo loop (revisión 1.2).
        """
        if self._periodic is not None and not self._periodic.done():
            return self._periodic

        async def _loop() -> None:
            interval = self._scan.interval_seconds
            logger.info("loop de escaneo periódico cada %d s", interval)
            while True:
                try:
                    await self.scan(origin=ORIGIN_PERIODIC)
                except Exception:
                    logger.exception("escaneo periódico falló; reintento en el siguiente tick")
                await asyncio.sleep(interval)

        self._periodic = asyncio.create_task(_loop())
        return self._periodic
