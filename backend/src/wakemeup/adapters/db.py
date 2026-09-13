"""Adaptador de persistencia: SQLite vía aiosqlite (AD-7).

Inventario de máquinas con upsert no destructivo (nunca borra): un host que
deja de responder permanece en la tabla; el borrado solo existe vía DELETE
explícito (AD-3), fuera del alcance de esta story.
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite

from wakemeup.core.models import Machine

_DEFAULT_DB_PATH = Path("wakemeup.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL UNIQUE,
    mac TEXT,
    hostname TEXT,
    updated_at TEXT NOT NULL
);
"""

_UPSERT = """
INSERT INTO machines (ip, mac, hostname, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(ip) DO UPDATE SET
    mac = CASE WHEN excluded.mac IS NOT NULL THEN excluded.mac ELSE machines.mac END,
    hostname = CASE WHEN excluded.hostname IS NOT NULL THEN excluded.hostname ELSE machines.hostname END,
    updated_at = excluded.updated_at;
"""

_LIST = "SELECT id, ip, mac, hostname FROM machines ORDER BY id;"


class Db:
    """Conexión a SQLite (aiosqlite). Abrir con `init_db`, cerrar con `close`."""

    def __init__(self, path: str | Path = _DEFAULT_DB_PATH) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def init_db(self) -> None:
        """Abre la conexión y crea el esquema si no existe."""
        self._conn = await aiosqlite.connect(self._path)
        # WAL: el loop periódico y las consultas API conviven sin bloqueos.
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def begin(self) -> None:
        """Inicia una transacción explícita (los upserts se agrupan en _run)."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._conn.execute("BEGIN")

    async def commit(self) -> None:
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._conn.commit()

    async def rollback(self) -> None:
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._conn.execute("ROLLBACK")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def upsert_machine(self, ip: str, mac: str | None, hostname: str | None) -> None:
        """Ejecuta el upsert SIN commit: el commit lo decide quien gestiona la
        transacción (DiscoveryService._run). En modo sin transacción explícita,
        SQLite legacy la abre con la primera DML y la cierra el commit del
        llamador."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._conn.execute(
            _UPSERT,
            (ip, mac, hostname, _now_iso()),
        )

    async def list_machines(self) -> list[Machine]:
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        rows = await self._conn.execute_fetchall(_LIST)
        return [Machine(id=r[0], ip=r[1], mac=r[2], hostname=r[3]) for r in rows]


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
