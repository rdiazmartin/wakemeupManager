"""Servicio de apagado por SSH con verificación de fingerprint (FR-7/AD-2/AC 2.3).

Antes de ejecutar el comando de `[shutdown]` se compara el fingerprint actual
del host con el fijado en el alta; si NO coincide → no se ejecuta NADA y la
máquina pasa a `no_fiable` (persistido en `machines.state`, AD-10) con el
evento registrado. Si coincide, el comando único `sudo -n systemctl poweroff`
(default) se ejecuta vía asyncssh como `remote_user` (sin shell libre); sin
sudoers NOPASSWD → error "sudo" claro (502). La confirmación de apagado real
llega del estado. Canal del registro: `api` para los tres endpoints (decisión
de planificación).
"""
from __future__ import annotations

import logging

from wakemeup.adapters.db import Db
from wakemeup.adapters.ssh import Ssh, SshError
from wakemeup.config import ShutdownSettings
from wakemeup.services.activity import ActivityService

logger = logging.getLogger(__name__)


class ShutdownError(Exception):
    """Error del apagado con motivo para el API (status_code + mensaje)."""

    def __init__(self, status_code: int, message: str, *, code: str = "conflict") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


class ShutdownService:
    """Apagado controlado: fingerprint estricto + comando único."""

    def __init__(
        self,
        db: Db,
        ssh: Ssh,
        activity: ActivityService,
        shutdown: ShutdownSettings,
    ) -> None:
        self._db = db
        self._ssh = ssh
        self._activity = activity
        self._shutdown = shutdown

    async def shutdown(self, machine_id: int, channel: str = "api", token: str = "-") -> dict:
        """Apaga la máquina tras verificar el fingerprint; devuelve `{"ok": true}`."""
        machine = await self._db.get_by_id(machine_id)
        if machine is None:
            raise ShutdownError(404, "máquina no encontrada", code="not_found")
        if machine.fingerprint is None or machine.remote_user is None:
            await self._record(channel, token, machine, "shutdown", "conflict")
            raise ShutdownError(409, "máquina no gestionada (haz el alta primero)", code="conflict")

        connection = None
        try:
            # La autenticación va con el par de claves del BE; la password de
            # un solo uso ya no existe.
            connection = await self._ssh.connect_key(
                machine.ip, machine.remote_user
            )
            current = await self._ssh.read_host_fingerprint(connection)
            if current != machine.fingerprint:
                # AD-2/AD-9: fingerprint distinto → NO ejecutar; no_fiable.
                logger.warning(
                    "fingerprint distinto en %s (esperado %s, actual %s); máquina no fiable",
                    machine.ip, machine.fingerprint, current,
                )
                await self._db.begin()
                try:
                    await self._db.set_no_fiable(machine_id)
                    await self._record_in_tx(channel, token, machine, "shutdown", "fingerprint_mismatch")
                    await self._db.commit()
                except Exception:
                    await self._db.rollback()
                    raise
                raise ShutdownError(
                    409,
                    "el host no coincide con el fingerprint fijado; máquina marcada no_fiable",
                    code="conflict",
                )
            await self._ssh.run_command(connection, self._shutdown.command)
        except SshError as exc:
            if "sudo" in str(exc).lower():
                await self._record(channel, token, machine, "shutdown", "sudo")
                raise ShutdownError(502, str(exc)) from exc
            await self._record(channel, token, machine, "shutdown", "error")
            raise ShutdownError(502, str(exc)) from exc
        finally:
            if connection is not None:
                await self._ssh.close(connection)

        await self._record(channel, token, machine, "shutdown", "ok")
        logger.info("shutdown enviado a %s (id %d)", machine.ip, machine_id)
        return {"ok": True}

    async def _record_in_tx(self, channel: str, token: str, machine, operation: str, result: str) -> None:
        """Registro dentro de la transacción YA abierta por el llamador."""
        await self._db.record_activity(
            channel, token, machine.id, machine.ip, operation, result
        )

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
