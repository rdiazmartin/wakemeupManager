"""Servicio de alta de máquinas con password de un solo uso (FR-6, AC 2.1).

Flujo: conectar por asyncssh con `usuario/password` (password solo en memoria
del proceso), leer el fingerprint `SHA256:<base64>` de la host key real ANTES
de instalar la clave (sin ventana de MITM), copiar la clave pública del BE a
`authorized_keys` de forma idempotente y finalmente `set_managed` (que marca
`no_fiable` hasta la verificación del primer apagado, AD-2). La password se
descarta en todos los caminos, incluido el fallo (FR-6).

Sobre una máquina ya gestionada → 409 (sin duplicar authorized_keys). La
máquina debe existir → 404. Password fallida → 401 con el envelope existente.
"""
from __future__ import annotations

import logging

from wakemeup.adapters.db import Db
from wakemeup.adapters.net import Net
from wakemeup.adapters.ssh import AuthFailedError, Ssh, SshError
from wakemeup.services.activity import ActivityService

logger = logging.getLogger(__name__)

STATE_NO_FIABLE = "no_fiable"


class EnrollmentError(Exception):
    """Error del alta con motivo para el API (status_code + mensaje)."""

    def __init__(self, status_code: int, message: str, *, code: str = "conflict") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


class EnrollmentService:
    """Alta de una máquina descubierta: SSH + fingerprint + authorized_keys."""

    def __init__(
        self, db: Db, net: Net, ssh: Ssh, activity: ActivityService
    ) -> None:
        self._db = db
        self._net = net
        self._ssh = ssh
        self._activity = activity

    async def enroll(
        self,
        machine_id: int,
        usuario: str,
        password: str,
        channel: str = "api",
        token: str = "-",
    ) -> dict:
        """Da de alta la máquina `machine_id` con usuario y password SSH.

        Devuelve `{"id": machine_id, "managed": True}` en éxito (matriz:
        respuesta 200). La password nunca se persiste ni se loguea; se
        descarta al cerrar la conexión y también en el fallo (FR-6).
        """
        machine = await self._db.get_by_id(machine_id)
        if machine is None:
            raise EnrollmentError(404, "máquina no encontrada", code="not_found")
        if machine.fingerprint is not None:
            # Ya gestionada: sin duplicar authorized_keys (matriz del spec).
            await self._record(channel, token, machine, "enroll", "conflict")
            raise EnrollmentError(409, "máquina ya gestionada", code="conflict")

        if not machine.mac:
            # Sin MAC no hay wake posterior; el alta sigue siendo válida (el
            # control por shutdown no la requiere), pero se avisa.
            logger.info("alta sin MAC: %s (wake no disponible)", machine.ip)

        connection = None
        try:
            connection = await self._ssh.connect(machine.ip, usuario, password)
            fingerprint = await self._ssh.read_host_fingerprint(connection)
            await self._ssh.install_authorized_key(connection)
        except AuthFailedError as exc:
            await self._record(channel, token, machine, "enroll", "auth_failed")
            raise EnrollmentError(401, str(exc), code="unauthorized") from exc
        except SshError as exc:
            await self._record(channel, token, machine, "enroll", "error")
            raise EnrollmentError(502, str(exc)) from exc
        finally:
            if connection is not None:
                await self._ssh.close(connection)
            # La password vence aquí aunque el alta falle (FR-6): al salir del
            # flujo, la referencia local y el argumento dejan de vivir.

        await self._db.begin()
        try:
            # TOCTOU: dos altas concurrentes pueden pasar la comprobación
            # anterior; re-verificar dentro de la transacción del alta (la
            # segunda ve fingerprint ya fijado → 409 sin duplicar la clave, y
            # sin chocar con el UNIQUE de `keys.machine_id` → 500).
            current = await self._db.get_by_id(machine_id)
            if current is None:
                await self._db.rollback()
                raise EnrollmentError(404, "máquina no encontrada", code="not_found")
            if current.fingerprint is not None:
                await self._db.record_activity(
                    channel, token, machine_id, machine.ip, "enroll", "conflict"
                )
                await self._db.commit()
                raise EnrollmentError(409, "máquina ya gestionada", code="conflict")
            await self._db.set_managed(machine_id, fingerprint, usuario)
            await self._db.record_key(machine_id, "ed25519", fingerprint)
            await self._db.record_activity(
                channel, token, machine_id, machine.ip, "enroll", "ok"
            )
            await self._db.commit()
        except EnrollmentError:
            await self._db.rollback()
            raise
        except Exception:
            await self._db.rollback()
            raise
        logger.info("máquina %s dada de alta (fingerprint %s)", machine.ip, fingerprint)
        return {"id": machine_id, "managed": True}

    async def _record(self, channel: str, token: str, machine, operation: str, result: str) -> None:
        """Registra la entrada de actividad en su propia transacción (FR-11).

        Cada registro abre/cierra transacción explícita (como los caminos de
        éxito): un INSERT fuera de transacción deja implícita abierta y el
        siguiente `db.begin()` del loop de estado fallaría.
        """
        await self._db.begin()
        try:
            await self._db.record_activity(
                channel, token, machine.id, machine.ip, operation, result
            )
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise
