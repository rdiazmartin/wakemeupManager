"""Adaptador de persistencia: SQLite vía aiosqlite (AD-7).

Inventario de máquinas con upsert no destructivo (nunca borra): un host que
deja de responder permanece en la tabla; el borrado solo existe vía DELETE
explícito (AD-3), fuera del alcance de la story 1.2. Tokens de dispositivo
(1.4): solo el hash SHA-256 de la representación hex-lower (AD-6).

Epic 2: columnas `fingerprint`/`remote_user` (alta, 2.1), tabla `keys`
(fingerprint + authorized_keys instalado) y tabla `activity_log` (2.5:
timestamp, canal, token, máquina, resultado — nunca passwords).
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite

from wakemeup.core.models import ActivityEntry, Machine, Token

_DEFAULT_DB_PATH = Path("wakemeup.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL UNIQUE,
    mac TEXT,
    hostname TEXT,
    state TEXT NOT NULL DEFAULT 'offline',
    status_checked_at TEXT,
    fingerprint TEXT,
    remote_user TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name TEXT NOT NULL UNIQUE,
    token_sha256 TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    machine_id INTEGER NOT NULL UNIQUE REFERENCES machines(id),
    algorithm TEXT NOT NULL,
    fingerprint_sha256 TEXT NOT NULL,
    installed TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    channel TEXT NOT NULL,
    token TEXT NOT NULL,
    machine_id INTEGER REFERENCES machines(id),
    machine_ip TEXT,
    operation TEXT NOT NULL,
    result TEXT NOT NULL
);
"""

# Migración de esquema para DBs creadas por stories anteriores: ALTER/creación
# solo de lo ausente (ver `_migrate`).
_COLUMN_MIGRATIONS = {
    "state": "ALTER TABLE machines ADD COLUMN state TEXT NOT NULL DEFAULT 'offline';",
    "status_checked_at": "ALTER TABLE machines ADD COLUMN status_checked_at TEXT;",
    "fingerprint": "ALTER TABLE machines ADD COLUMN fingerprint TEXT;",
    "remote_user": "ALTER TABLE machines ADD COLUMN remote_user TEXT;",
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
WHERE ip = ? AND state != 'no_fiable';
"""

_LIST = (
    "SELECT id, ip, mac, hostname, state, status_checked_at, fingerprint, remote_user "
    "FROM machines ORDER BY id;"
)

_GET_STATUS = "SELECT state, status_checked_at FROM machines WHERE ip = ?;"

_GET_BY_ID = (
    "SELECT id, ip, mac, hostname, state, status_checked_at, fingerprint, remote_user "
    "FROM machines WHERE id = ?;"
)

_SET_MANAGED = (
    "UPDATE machines SET fingerprint = ?, remote_user = ?, updated_at = ? WHERE id = ?;"
)

_SET_NO_FIABLE = "UPDATE machines SET state = 'no_fiable', updated_at = ? WHERE id = ?;"

_SET_STATE = "UPDATE machines SET state = ?, updated_at = ? WHERE id = ?;"

_INSERT_KEY = (
    "INSERT INTO keys (machine_id, algorithm, fingerprint_sha256, installed, created_at) "
    "VALUES (?, ?, ?, ?, ?);"
)

_KEY_COUNT = "SELECT COUNT(*) FROM keys WHERE machine_id = ?;"

_INSERT_ACTIVITY = (
    "INSERT INTO activity_log (timestamp, channel, token, machine_id, machine_ip, operation, result) "
    "VALUES (?, ?, ?, ?, ?, ?, ?);"
)

_LIST_ACTIVITY = (
    "SELECT id, timestamp, channel, token, machine_id, machine_ip, operation, result "
    "FROM activity_log ORDER BY id DESC LIMIT ?;"
)

_ACTIVITY_STAT = "SELECT operation, result, COUNT(*) FROM activity_log GROUP BY operation, result ORDER BY operation;"

_INSERT_TOKEN = "INSERT INTO tokens (device_name, token_sha256, created_at, revoked_at) VALUES (?, ?, ?, ?);"

_TOKEN_EXISTS = "SELECT 1 FROM tokens WHERE token_sha256 = ? AND revoked_at IS NULL;"

_REVOKE_TOKEN = "UPDATE tokens SET revoked_at = ? WHERE token_sha256 = ? AND revoked_at IS NULL;"

_REVOKE_TOKEN_NAME = "UPDATE tokens SET revoked_at = ? WHERE device_name = ? AND revoked_at IS NULL;"

_LIST_TOKENS = "SELECT id, device_name, token_sha256, created_at, revoked_at FROM tokens ORDER BY id;"


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
        """Evoluciona DBs creadas por stories anteriores (1.2/1.3/1.4 → epic 2).

        Añade las columnas ausentes a `machines` y garantiza las tablas
        `tokens`/`keys`/`activity_log` re-ejecutando el `_SCHEMA` canónico
        (CREATE TABLE IF NOT EXISTS, fuente única de verdad). Consulta
        `PRAGMA table_info` en lugar de tragar cualquier error de ALTER: un
        fallo real (DB bloqueada, disco lleno...) se propaga.
        """
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._migrate_machines()
        await self._conn.executescript(_SCHEMA)

    async def _migrate_machines(self) -> None:
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
            Machine(
                id=r[0], ip=r[1], mac=r[2], hostname=r[3], state=r[4],
                status_checked_at=r[5], fingerprint=r[6], remote_user=r[7],
            )
            for r in rows
        ]

    async def get_by_id(self, machine_id: int) -> Machine | None:
        """Máquina del inventario por id; `None` si no existe."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        rows = await self._conn.execute_fetchall(_GET_BY_ID, (machine_id,))
        if not rows:
            return None
        r = rows[0]
        return Machine(
            id=r[0], ip=r[1], mac=r[2], hostname=r[3], state=r[4],
            status_checked_at=r[5], fingerprint=r[6], remote_user=r[7],
        )

    async def set_managed(self, machine_id: int, fingerprint: str, remote_user: str) -> None:
        """Fija el fingerprint y el usuario remoto tras un alta correcta (2.1).

        NO toca `state`: el alta deja la máquina operable (AC 2.1: "puede
        apagarse") y el estado real online/offline lo fija el loop de estado.
        `no_fiable` es SOLO para el mismatch de fingerprint en el apagado
        (AD-2) — marcarlo aquí dejaba la máquina atascada sin acciones en la
        app (sin vuelta atrás). Idempotente: el alta sobre una máquina ya
        gestionada se rechaza en el servicio (409), nunca reinstala la clave.
        """
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._conn.execute(
            _SET_MANAGED, (fingerprint, remote_user, _now_iso(), machine_id)
        )

    async def set_no_fiable(self, machine_id: int) -> None:
        """Marca la máquina `no_fiable` (fingerprint actual != fijado, AD-2/AD-10)."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._conn.execute(_SET_NO_FIABLE, (_now_iso(), machine_id))

    async def set_state(self, machine_id: int, state: str) -> None:
        """Escribe el estado persistido de la máquina (sin TTL; epic 2)."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._conn.execute(_SET_STATE, (state, _now_iso(), machine_id))

    async def record_key(self, machine_id: int, algorithm: str, fingerprint_sha256: str) -> None:
        """Registra el par de claves + fingerprint instalado en el alta (tabla `keys`).

        `installed == 'authorized_keys'` indica que la clave pública del BE se
        copió de forma idempotente a `authorized_keys` del usuario remoto.
        """
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._conn.execute(
            _INSERT_KEY,
            (machine_id, algorithm, fingerprint_sha256, "authorized_keys", _now_iso()),
        )

    async def key_count(self, machine_id: int) -> int:
        """Nº de registros en `keys` para la máquina (0 = nunca dada de alta)."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        rows = await self._conn.execute_fetchall(_KEY_COUNT, (machine_id,))
        return rows[0][0] if rows else 0

    async def record_activity(
        self,
        channel: str,
        token: str,
        machine_id: int | None,
        machine_ip: str | None,
        operation: str,
        result: str,
    ) -> None:
        """Registra una entrada de actividad (FR-11, story 2.5).

        NUNCA passwords (la password del alta nunca llega aquí); el token se
        registra como identificación del dispositivo (no es secreto en el
        registro porque viaja en cada petición; los logs no lo exponen fuera).
        """
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._conn.execute(
            _INSERT_ACTIVITY,
            (_now_iso(), channel, token, machine_id, machine_ip, operation, result),
        )

    async def list_activity(self, limit: int = 20) -> list[ActivityEntry]:
        """Últimas entradas de actividad (más recientes primero), `limit` tope."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        rows = await self._conn.execute_fetchall(_LIST_ACTIVITY, (max(limit, 1),))
        return [
            ActivityEntry(
                id=r[0], timestamp=r[1], channel=r[2], token=r[3],
                machine_id=r[4], machine_ip=r[5], operation=r[6], result=r[7],
            )
            for r in rows
        ]

    async def activity_stats(self) -> list[tuple[str, str, int]]:
        """Agregados por `(operation, result)` para `wakemeup-cli activity stat`."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        rows = await self._conn.execute_fetchall(_ACTIVITY_STAT)
        return [(r[0], r[1], r[2]) for r in rows]

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
        y marca; nunca inserta filas ni altera `updated_at`. NUNCA sobrescribe
        una fila `no_fiable` (AD-10/AD-2): el mismatch de fingerprint debe
        sobrevivir a los barridos del loop de estado. Si la IP no existe en el
        inventario, no hace nada."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._conn.execute(
            _UPSERT_STATUS,
            (state, _now_iso(), ip),
        )

    async def create_token(self, device_name: str, token_sha256: str) -> None:
        """Registra un token por su hash (AD-6). El token plano no toca la BD."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        await self._conn.execute(
            _INSERT_TOKEN, (device_name, token_sha256, _now_iso(), None)
        )

    async def token_exists(self, token_sha256: str) -> bool:
        """¿Hay un token activo (no revocado) con este hash?"""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        row = await self._conn.execute_fetchall(
            _TOKEN_EXISTS, (token_sha256,)
        )
        return bool(row)

    async def revoke_token(self, token_sha256: str) -> bool:
        """Revoca el token cuyo hash coincide; `True` si existía y se revocó.

        Un token ya revocado o inexistente devuelve `False` sin error.
        """
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        cursor = await self._conn.execute(
            _REVOKE_TOKEN, (_now_iso(), token_sha256)
        )
        return cursor.rowcount > 0

    async def revoke_token_by_name(self, device_name: str) -> bool:
        """Revoca por nombre de dispositivo (CLI); `False` si no existe/ya revocado."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        cursor = await self._conn.execute(
            _REVOKE_TOKEN_NAME, (_now_iso(), device_name)
        )
        return cursor.rowcount > 0

    async def list_tokens(self) -> list[Token]:
        """Tokens registrados (activos y revocados), ordenados por creación."""
        if self._conn is None:
            raise RuntimeError("db no inicializado: llamar init_db() primero")
        rows = await self._conn.execute_fetchall(_LIST_TOKENS)
        return [
            Token(
                id=r[0],
                device_name=r[1],
                token_sha256=r[2],
                created_at=r[3],
                revoked_at=r[4],
            )
            for r in rows
        ]


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()
