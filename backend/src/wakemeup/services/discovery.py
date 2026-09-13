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

from wakemeup.adapters.db import Db
from wakemeup.adapters.net import Net
from wakemeup.config import ScanSettings

logger = logging.getLogger(__name__)


class DiscoveryService:
    """Orquestación del escaneo: ping+ARP → upsert, con singleflight."""

    def __init__(self, net: Net, db: Db, scan: ScanSettings) -> None:
        self._net = net
        self._db = db
        self._scan = scan
        self._lock = asyncio.Lock()
        self._current: asyncio.Task[int] | None = None
        self._in_flight = False
        self._periodic: asyncio.Task | None = None

    async def scan(self) -> int:
        """Ejecuta un escaneo o se une al escaneo en marcha (singleflight, AD-3).

        Devuelve el nº de hosts upserted. Llamadas concurrentes → una sola
        ejecución efectiva: el segundo `scan()` espera y comparte el resultado.
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

        return await current

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

    @property
    def in_flight(self) -> bool:
        """¿Hay un escaneo corriendo ahora mismo?"""
        return self._in_flight

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
                    await self.scan()
                except Exception:
                    logger.exception("escaneo periódico falló; reintento en el siguiente tick")
                await asyncio.sleep(interval)

        self._periodic = asyncio.create_task(_loop())
        return self._periodic
