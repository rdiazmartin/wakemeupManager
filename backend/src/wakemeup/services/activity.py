"""Servicio de registro de actividad (FR-11, story 2.5).

Cada acción de control (alta, wake, shutdown) deja una entrada con timestamp,
canal (`api|mcp`), token, máquina y resultado contra `activity_log`. El shape
permite computar SM-1..SM-4 sin atribución personal; NUNCA passwords (la
password del alta no cruza este servicio) ni el token plano como clave de
búsqueda (solo su digest hex-lower, AD-6).
"""
from __future__ import annotations

import logging

from wakemeup.adapters.db import Db

logger = logging.getLogger(__name__)

CHANNEL_API = "api"
CHANNEL_MCP = "mcp"


class ActivityService:
    """Registro y consulta de actividad (el canal `mcp` llega en el epic 3)."""

    def __init__(self, db: Db) -> None:
        self._db = db

    async def record(
        self,
        channel: str,
        token: str,
        machine_id: int | None,
        machine_ip: str | None,
        operation: str,
        result: str,
    ) -> None:
        """Registra una entrada. El resultado es un código corto (`ok`,
        `auth_failed`, `conflict`, `fingerprint_mismatch`, `sudo`, `error`…)."""
        await self._db.record_activity(
            channel=channel,
            token=token,
            machine_id=machine_id,
            machine_ip=machine_ip,
            operation=operation,
            result=result,
        )

    async def list(self, limit: int = 20):
        """Últimas entradas (recientes primero)."""
        return await self._db.list_activity(limit=limit)

    async def stats(self) -> list[tuple[str, str, int]]:
        """Agregados `(operation, result, count)` para la CLI."""
        return await self._db.activity_stats()
