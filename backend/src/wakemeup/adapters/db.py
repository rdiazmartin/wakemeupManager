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
    state TEXT NOT NULL DEFAULT 'offline',
    status_checked_at TEXT,
    updated_at TEXT NOT NULL
);
"""

# Migración segura para DBs creadas por la story 1.2 (sin las columnas de
# estado): ALTER TABLE solo de las columnas ausentes (ver `_migrate`).
_COLUMN_MIGRATIONS = {
    "state": "ALTER TABLE machines ADD COLUMN state TEXT NOT NULL DEFAULT 'offline';",
    "status_checked_at": "ALTER TABLE machines ADD COLUMN status_checked_at TEXT;",
}

_UPSERT = """
INSERT INTO machines (ip, mac, hostname, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(ip) DO UPDATE SET
    mac = CASE WHEN excluded.mac IS NOT NULL THEN excluded.mac ELSE machines.mac END,
    hostname = CASE WHEN excluded.hostname IS NOT NULL THEN excluded.hostname ELSE machines.hostname END,
    updated_at = excluded.updated_at;
"""

_UPSERT_STATUS = """
UPDATE machines SET state = ?, status_checked_at = ?
WHERE ip = ?;
"""

_LIST = "SELECT id, ip, mac, hostname, state, status_checked_at FROM machines ORDER BY id;"

_GET_STATUS = "SELECT state, status_checked_at FROM machines WHERE ip = ?;"


class Db:
    """Conexión a SQLite (aiosqlite). Abrir con `init_db`, cerrar con `close`."""

    def __init__(self, path: str | Path = _DEFAULT_DB_PATH) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def init_db(self) -> None:
        """Abre la conexión y crea/evoluciona el esquema (migración 1.2 → 1.3)."""
        self._conn = await aiosqlite.connect(self._path)
        # WAL: el loop periódico y las consultas API conviven sin bloqueos.
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.executescript(_SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Añade solo las columnas de estado ausentes (DBs de la story 1.2).

        Consulta `PRAGMA table_info` en lugar de tragar cualquier error de
        ALTER: un fallo real (DB bloqueada, disco lleno...) se propaga.
        """
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        rows = await self._conn.execute_fetchall("PRAGMA table_info(machines);")
        existing = {row[1] for row in rows}
        for column, ddl in _COLUMN_MIGRATIONS.items():
            if column not in existing:
                await self._conn.execute(ddl)

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
        return [
            Machine(id=r[0], ip=r[1], mac=r[2], hostname=r[3], state=r[4], status_checked_at=r[5])
            for r in rows
        ]

    async def get_status(self, ip: str) -> tuple[str, str | None] | None:
        """Estado persistido de la máquina: `(state, status_checked_at)` o `None` si no existe."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        row = await self._conn.execute_fetchall(_GET_STATUS, (ip,))
        if not row:
            return None
        return row[0][0], row[0][1]

    async def set_status(self, ip: str, state: str) -> None:
        """Guarda el estado comprobado SIN commit: lo decide quien gestiona la
        transacción (StatusService.check_all). UPDATE directo: solo toca estado
        y marca; nunca inserta filas ni altera `updated_at`. Si la IP no existe
        en el inventario, no hace nada."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._conn.execute(
            _UPSERT_STATUS,
            (state, _now_iso(), ip),
        )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
