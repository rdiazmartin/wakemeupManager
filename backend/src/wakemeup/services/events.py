"""Bus de eventos de estado (AD-11, epic 3 / story 3.3).

Pub/sub asyncio en proceso: los productores (loop de estado, acciones de
control de API/MCP, escaneo) publican un `Event` tipado y los suscriptores
(el stream SSE de `/api/v1/events`) lo reciben. Reglas del AD-11/v1:

- **Sin suscriptores → el evento se omite**: no hay cola de entrega ni replay.
- **Nunca bloquea a los publicadores**: cada suscriptor tiene una cola acotada
  y un `put_nowait`; si está llena, el evento se descarta para ese suscriptor
  (un cliente lento no frena al BE).
- La persistencia en el registro de actividad (FR-11) la hace quien produce la
  acción (los servicios ya registran su entrada); el bus solo transporta.

El evento transporta `{type, machine, timestamp, origin}` y NUNCA credenciales
ni claves (FR-11).
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from wakemeup.core.models import Event

logger = logging.getLogger(__name__)

# Cola por suscriptor: acota la memoria ante un cliente que no consume. Al
# llenarse, los eventos nuevos se descartan (v1 sin cola de entrega, AD-11).
_SUBSCRIBER_QUEUE_MAXSIZE = 256


class EventBus:
    """Bus pub/sub asyncio (AD-11). Seguro para un único event loop."""

    def __init__(self, maxsize: int = _SUBSCRIBER_QUEUE_MAXSIZE) -> None:
        self._maxsize = maxsize
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._running = False

    def start(self) -> None:
        """Marca el bus como activo (lifespan de la app). Idempotente."""
        self._running = True

    def stop(self) -> None:
        """Para el bus y descarta los suscriptores pendientes (lifespan)."""
        self._running = False
        self._subscribers.clear()

    @property
    def running(self) -> bool:
        """¿Está el bus arrancado (lifespan activo)?"""
        return self._running

    def publish(self, event: Event) -> None:
        """Emite `event` a los suscriptores; sin suscriptores → se omite.

        Nunca bloquea: cada entrega es `put_nowait`; si la cola de un
        suscriptor está llena, el evento se descarta solo para ese suscriptor
        (cliente lento) y el resto sigue recibiendo.
        """
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "suscriptor de eventos lento: evento %s descartado", event.type
                )

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        """Suscriptor del bus: cede una cola y la elimina al salir.

        Al desconectar el cliente, el bloque `finally` retira la cola para que
        el bus deje de entregarle eventos (no rompe la emisión del resto).
        """
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        """Nº de suscriptores activos (tests y diagnóstico)."""
        return len(self._subscribers)
