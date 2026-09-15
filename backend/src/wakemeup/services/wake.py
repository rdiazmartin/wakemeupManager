"""Servicio de encendido por WOL (FR-4/FR-5, AC 2.2).

Valida la MAC de la máquina (422 si inválida/cero/multicast), envía UN único
magic packet al broadcast por la interfaz Ethernet si existe y devuelve éxito
(aunque la máquina tarde en arrancar; la confirmación llega del estado, FR-2).
Sin retry automático (FR-4): un envío por petición. Sin Ethernet emisora →
el envío se reporta como éxito degradado (warning en GET /status, nota de
diseño 2.2) en lugar de bloquear.
"""
from __future__ import annotations

import logging

from wakemeup.adapters.db import Db
from wakemeup.adapters.net import Net, NoWolInterfaceError
from wakemeup.core.models import Event
from wakemeup.services.activity import ActivityService
from wakemeup.services.events import EventBus

logger = logging.getLogger(__name__)


class WakeError(Exception):
    """Error del wake con motivo para el API (status_code + mensaje)."""

    def __init__(self, status_code: int, message: str, *, code: str = "conflict") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


class WakeService:
    """Wake on LAN: MAC validada + un único magic packet."""

    def __init__(
        self, db: Db, net: Net, activity: ActivityService, events: EventBus | None = None
    ) -> None:
        self._db = db
        self._net = net
        self._activity = activity
        self._events = events

    async def wake(
        self, machine_id: int, channel: str = "api", token: str = "-"
    ) -> dict:
        """Envía el wake de la máquina y devuelve `{"ok": true}` siempre.

        Errores acotados: 404 (máquina inexistente), 409 (no gestionada o sin
        MAC) y 422 (MAC inválida) — matriz I/O del spec 2.2.
        """
        machine = await self._db.get_by_id(machine_id)
        if machine is None:
            raise WakeError(404, "máquina no encontrada", code="not_found")
        if machine.fingerprint is None:
            await self._record(channel, token, "wake", machine, "conflict")
            raise WakeError(409, "máquina no gestionada (haz el alta primero)", code="conflict")
        mac = machine.mac
        if not mac:
            await self._record(channel, token, "wake", machine, "conflict")
            raise WakeError(409, "máquina sin MAC fijada; no se puede enviar WOL", code="conflict")
        try:
            self._net.normalize_mac(mac)
        except ValueError as exc:
            await self._record(channel, token, "wake", machine, "validation_error")
            raise WakeError(422, f"MAC inválida: {exc}", code="validation_error") from exc

        try:
            await self._net.send_wol(mac)
            result = "ok"
        except NoWolInterfaceError:
            # Sin Ethernet emisora: éxito degradado + warning (FR-4/FR-5).
            logger.warning("wake de %s sin interfaz Ethernet: éxito degradado (warning)", machine.ip)
            result = "ok_no_ethernet"
        except (RuntimeError, OSError) as exc:
            # Un envío que no se pudo emitir (p. ej. ambos puertos UDP fallan o
            # EPERM socket) también degrada: el endpoint responde éxito y el
            # warning queda en el log (nunca 5xx; la confirmación llega del estado).
            logger.warning("wake de %s no emitido (%s); éxito degradado", machine.ip, exc)
            result = "ok_degraded"
        await self._record(channel, token, "wake", machine, result, stamp_origin=True)
        self._publish(channel, "wake_sent", machine)
        logger.info("wake enviado a %s (id %d)", machine.ip, machine_id)
        return {"ok": True}

    def _publish(self, channel: str, event_type: str, machine) -> None:
        """Emite el evento del bus con el origen del canal (AD-11, epic 3).

        `wake_machine` del MCP emite `wake_sent` con origen `mcp` (la app
        notifica "El agente IA encendió la máquina"); la transición real
        offline→online posterior la emite el loop de estado como `periodic`.
        """
        if self._events is None:
            return
        from datetime import datetime, timezone

        self._events.publish(
            Event(
                type=event_type,
                machine=machine.hostname or machine.ip,
                timestamp=datetime.now(timezone.utc).isoformat(),
                origin=channel,
                machine_id=machine.id,
                machine_ip=machine.ip,
            )
        )

    async def _record(
        self, channel: str, token: str, operation: str, machine, result: str,
        stamp_origin: bool = False,
    ) -> None:
        """Registra la entrada de actividad en su propia transacción (FR-11).

        Cada registro abre/cierra transacción explícita (como los caminos de
        éxito): un INSERT fuera de transacción deja implícita abierta y el
        siguiente `db.begin()` del loop de estado fallaría. `stamp_origin`
        persiste el origen del canal como último cambio (fallback de polling,
        FR-18), en la misma transacción.
        """
        await self._db.begin()
        try:
            await self._db.record_activity(
                channel, token, machine.id, machine.ip, operation, result
            )
            if stamp_origin:
                await self._db.set_last_change(machine.id, channel)
            await self._db.commit()
        except Exception:
            await self._db.rollback()
            raise
