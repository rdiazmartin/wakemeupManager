"""App FastAPI del BE de wakemeupManager (AD-1).

Prefijo `/api/v1` con healthcheck exento (decisión 1.2), auth Bearer por token
de dispositivo con backoff (FR-10/AD-6), inventario (AD-10) y escaneo forzado
(AD-3). Epic 2 (ciclo de control): alta asyncssh con password de un solo uso
(2.1), wake WOL (2.2) y shutdown con verificación de fingerprint (2.3), todos
con registro de actividad (2.5). El API nunca ejecuta acciones directamente:
delega en los servicios (AD-4). El lifespan conecta los loops periódicos de
discovery y status al ciclo de vida de la app, inicializa la DB y los detiene
al apagar.

Epic 3 (agente IA y eventos): bus de eventos asyncio (`app.events`, AD-11),
stream SSE en `GET /api/v1/events`, servidor MCP montado bajo `/api/v1/mcp`
con auth dedicada solo tailnet+loopback (AD-12) y `last_origin`/`last_change_at`
aditivos en el DTO de máquina.

Los servicios se exponen como atributos de `app` (sustituibles en tests sin
abrir la DB real); `create_app` construye el grafo desde la configuración y
`app` es la instancia por defecto (la que arranca uvicorn y los tests).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, StringConstraints
from starlette.applications import Starlette

from wakemeup import __version__
from wakemeup.adapters.db import Db
from wakemeup.adapters.net import Net, is_trusted_client
from wakemeup.adapters.ssh import Ssh
from wakemeup.config import Settings
from wakemeup.core.models import MachineDTO
from wakemeup.services.activity import ActivityService, CHANNEL_API, CHANNEL_MCP
from wakemeup.services.auth import AuthService, sha256_hex_lower
from wakemeup.services.discovery import DiscoveryService, ORIGIN_SCAN
from wakemeup.services.enrollment import EnrollmentError, EnrollmentService
from wakemeup.services.events import EventBus
from wakemeup.services.shutdown import ShutdownError, ShutdownService
from wakemeup.services.status import StatusService
from wakemeup.services.wake import WakeError, WakeService
from wakemeup.api.mcp import MCP_PATH, create_mcp_server

logger = logging.getLogger(__name__)

_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "too_many_requests",
}

# Prefijo de las rutas REST (AD-1). El MCP vive bajo el mismo prefijo.
_API_PREFIX = "/api/v1"


def _build_services() -> dict[str, Any]:
    """Grafo de servicios desde la configuración (sin abrir la DB todavía).

    El bus de eventos (AD-11) se construye aquí y se inyecta en los servicios
    que emiten (status/discovery/wake/shutdown/enrollment) para que toda acción
    quede en el stream con su origen.
    """
    settings = Settings()
    db = Db(settings.db.path)
    net = Net()
    activity = ActivityService(db=db)
    ssh = Ssh(settings=settings.ssh)
    events = EventBus()
    return {
        "db": db,
        "net": net,
        "discovery": DiscoveryService(
            net=net, db=db, scan=settings.scan, events=events, activity=activity
        ),
        "status": StatusService(net=net, db=db, scan=settings.scan, events=events),
        "auth": AuthService(db=db, settings=settings.auth),
        "enrollment": EnrollmentService(
            db=db, net=net, ssh=ssh, activity=activity, events=events
        ),
        "wake": WakeService(db=db, net=net, activity=activity, events=events),
        "shutdown": ShutdownService(
            db=db, ssh=ssh, activity=activity, shutdown=settings.shutdown, events=events
        ),
        "activity": ActivityService(db=db),
        "events": events,
    }


def _host_header_pattern(host: str) -> str:
    """Patrón de `Host` header para `allowed_hosts` con puerto comodín.

    Un literal IPv6 necesita corchetes en el `Host` header (`[fd7a::1]:8080`);
    sin ellos el patrón (`fd7a::1:*`) es malformado y no casaría nunca. Los
    nombres (p. ej. el DNSName de tailscale / MagicDNS) se usan tal cual.
    """
    import ipaddress

    try:
        is_ipv6 = ipaddress.ip_address(host).version == 6
    except ValueError:
        is_ipv6 = False
    return f"[{host}]:*" if is_ipv6 else f"{host}:*"


def _build_transport_security():
    """`TransportSecuritySettings` para el host header del MCP (AD-12).

    Habilita la protección contra DNS rebinding y permite los hosts de la
    tailnet/loopback (`bind_hosts` de `[api]` + loopback) más los nombres
    extra (`extra_allowed_hosts`, p. ej. el DNSName de tailscale/MagicDNS),
    todos con puerto comodín. El guard de IP de la capa FastAPI
    (`is_trusted_client`) es la defensa real; esto añade la validación del
    `Host` que exige el SDK.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    settings = Settings()
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    for host in (*settings.api.bind_hosts, *settings.api.extra_allowed_hosts):
        if not host:
            continue
        pattern = _host_header_pattern(host)
        if pattern not in allowed_hosts:
            allowed_hosts.append(pattern)
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
    )


@asynccontextmanager
async def _lifespan(application: FastAPI):
    """Arranque/apagado: DB + loops periódicos (FR-2/FR-3) + bus + MCP.

    El submount del MCP NO ejecuta su propio lifespan, así que el host entra
    aquí `mcp_session_manager.run()` (nota de diseño 3.1); arranca y para el
    bus de eventos en el mismo ciclo. Consume el grafo expuesto en `app.*`
    (sustituible en tests) y espera a los loops cancelados antes de cerrar la DB.
    """
    await application.db.init_db()
    application.events.start()
    t_disc = application.discovery.periodic_task()
    t_status = application.status.check_cycle()
    try:
        async with application.mcp_session_manager.run():
            yield
    finally:
        application.discovery.stop()
        application.status.stop()
        for task in (t_disc, t_status):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # puede fallar al cancelarse
                pass
        application.events.stop()
        await application.db.close()


def create_app(
    services: dict[str, Any] | None = None,
    *,
    transport_security=None,
) -> FastAPI:
    """Construye una app FastAPI con su propio grafo y sesión MCP.

    `services` permite inyectar dobles en tests; `transport_security` permite
    ajustar el host permitido del MCP (p. ej. `testserver` en TestClient). Cada
    llamada crea un `MCPServer`/session manager nuevos: el session manager solo
    puede `run()` una vez por instancia (el lifespan de tests usa una app nueva).
    """
    graph = services if services is not None else _build_services()
    app = FastAPI(title="wakemeup-backend", version=__version__, lifespan=_lifespan)
    for name, service in graph.items():
        setattr(app, name, service)

    # Servidor MCP (3.1): las tools resuelven el grafo `app.*` en cada llamada
    # para usar los mismos servicios que la API (AD-12) y ser sustituibles en
    # tests. Se crea antes de montarlo para exponer `mcp_session_manager`.
    mcp_server = create_mcp_server(lambda: app)
    mcp_sub_app: Starlette = mcp_server.streamable_http_app(
        streamable_http_path="/",
        transport_security=(
            transport_security if transport_security is not None else _build_transport_security()
        ),
    )
    app.mcp = mcp_server
    app.mcp_session_manager = mcp_server.session_manager

    _register_routes(app, mcp_sub_app)
    return app


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    """Envelope uniforme de error (AD-1): `{"error": {"code", "message"}}`."""
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def _bearer_token(request: Request) -> tuple[str | None, str | None]:
    """Devuelve `(token_plano, digest_hex)` del Bearer presentado (o Nones)."""
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else None
    digest = sha256_hex_lower(token) if token is not None else None
    return token, digest


def _is_mcp_path(path: str) -> bool:
    """¿Es una ruta del servidor MCP (`/api/v1/mcp` o sub-ruta)?"""
    return path == MCP_PATH or path.startswith(MCP_PATH + "/")


def _is_backed_off(auth: AuthService, ip: str, digest: str | None) -> bool:
    return auth.backoff.is_blocked(ip) or (
        digest is not None and auth.backoff.is_blocked(digest)
    )


async def _record_mcp_auth_failure(app: FastAPI, result: str) -> None:
    """Registra el fallo de auth del MCP en el registro de actividad (FR-11).

    Best-effort: nunca debe tumbar la respuesta de auth (la DB puede no estar
    inicializada en tests/arranque); los errores solo se loguean.
    """
    activity = getattr(app, "activity", None)
    db = getattr(app, "db", None)
    if activity is None or db is None:
        return
    try:
        await db.begin()
        try:
            await activity.record(CHANNEL_MCP, "-", None, None, "mcp_auth", result)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
    except Exception as exc:  # best-effort: jamás propaga a la respuesta
        logger.warning("no se pudo registrar el fallo de auth del MCP: %r", exc)


class _EnrollBody(BaseModel):
    """Cuerpo de `POST /machines/{id}/enroll` (2.1): usuario + password de un solo uso.

    La password vive solo en memoria del proceso (FR-6/AD-5): nunca en disco,
    logs, argv, entorno ni cuerpo de respuesta; se descarta también si falla.
    `strip_whitespace` + límites acotados: credenciales blank/oversize → 422
    antes de llegar al SSH.
    """

    usuario: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]
    password: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1024)]


def _request_token(request: Request) -> str:
    """Digest del token Bearer presentado (registro de actividad, AD-6).

    En el log se guarda el hash hex-lower, nunca el token plano (FR-11).
    """
    return _bearer_token(request)[1] or "-"


def _register_routes(app: FastAPI, mcp_sub_app: Starlette) -> None:
    """Registra middleware, endpoints y el montaje del MCP en `app`."""
    api = APIRouter(prefix=_API_PREFIX)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        code = _ERROR_CODES.get(exc.status_code, "error")
        logger.warning("HTTP %s: %s", exc.status_code, exc.detail)
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        """422 con envelope uniforme (AD-1): FastAPI usa `{"detail": [...]}` por
        defecto, que rompería el contrato; el motivo va en el mensaje."""
        logger.warning("validación fallida: %s", exc.errors())
        return _error(422, "validation_error", "cuerpo de petición inválido")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
        """5xx con motivo en el cuerpo (catálogo FR-9), sin filtrar detalles."""
        logger.exception("error interno del BE: %r", exc)
        return _error(500, "internal_error", "error interno del servidor")

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        """Bearer token obligatorio en `/api/v1/*`, salvo exenciones (FR-10, AD-6).

        Epic 3: las rutas del MCP quedan exentas del token de DISPOSITIVO — su
        propia auth (token `mcp`) se resuelve en `mcp_auth_middleware`.
        """
        service_app = request.app
        if not request.url.path.startswith(_API_PREFIX):
            return await call_next(request)
        if _is_mcp_path(request.url.path):
            return await call_next(request)
        if AuthService.is_exempt(request.method, request.url.path):
            return await call_next(request)

        token, digest = _bearer_token(request)
        ip = request.client.host if request.client is not None else "unknown"

        if _is_backed_off(service_app.auth, ip, digest):
            return _error(
                429,
                "too_many_requests",
                "demasiados intentos de autenticación; reintente más tarde",
            )
        if not await service_app.auth.authenticate(token, ip, kind="device"):
            return _error(401, "unauthorized", "token de dispositivo inválido o ausente")
        return await call_next(request)

    @app.middleware("http")
    async def mcp_auth_middleware(request: Request, call_next):
        """Auth + guard solo-tailnet del servidor MCP (AD-12, story 3.2).

        Un token de dispositivo NO abre el MCP y un token MCP NO abre REST/SSE
        (FR-10b). Además el cliente debe venir de loopback/tailnet: la LAN
        física o fuera de la VPN → 403 (NFR-1). Los fallos de auth se registran
        (FR-11) vía el backoff/el log del `AuthService`.
        """
        if not _is_mcp_path(request.url.path):
            return await call_next(request)
        service_app = request.app

        ip = request.client.host if request.client is not None else "unknown"
        if not is_trusted_client(ip):
            # Solo log: persistir aquí permitiría a clientes no autenticados de
            # la LAN escribir filas ilimitadas en activity_log (sin backoff).
            logger.warning("MCP rechazado desde origen no loopback/tailnet: %s", ip)
            return _error(403, "forbidden", "el MCP solo es accesible desde tailnet/loopback")

        token, digest = _bearer_token(request)
        if _is_backed_off(service_app.auth, ip, digest):
            await _record_mcp_auth_failure(service_app, "backoff")
            return _error(
                429,
                "too_many_requests",
                "demasiados intentos de autenticación; reintente más tarde",
            )
        if not await service_app.auth.authenticate(token, ip, kind="mcp"):
            await _record_mcp_auth_failure(
                service_app, "missing_token" if token is None else "invalid_token"
            )
            return _error(401, "unauthorized", "token MCP inválido o ausente")
        return await call_next(request)

    @api.get("/status")
    async def status(request: Request) -> dict:
        """Healthcheck (exento de auth): estado/versión + interfaz WOL (FR-5).

        Sin interfaz Ethernet emisora, el BE sigue vivo pero reporta `warning`
        (WOL no fiable vía WiFi); el healthcheck nunca bloquea.
        """
        wl = await request.app.net.wol_interface()
        return {
            "status": "ok" if wl is not None else "warning",
            "version": __version__,
            "wol": {"interface": wl},
        }

    @api.head("/status")
    async def status_head() -> None:
        """HEAD del healthcheck (monitores/curl -I): exento de auth, sin cuerpo."""
        return None

    @api.get("/machines")
    async def machines(request: Request) -> dict:
        """Listado con DTO AD-10 completo; `managed` sale de `fingerprint IS NOT NULL`.

        Epic 3: se añaden `last_origin`/`last_change_at` de forma ADITIVA para el
        fallback de notificación por polling de la app (stream SSE caído).
        """
        rows = await request.app.status.list_with_status()
        machines = [
            asdict(
                MachineDTO(
                    id=m.id,
                    name=m.hostname or m.ip,
                    ip=m.ip,
                    mac=m.mac,
                    hostname=m.hostname,
                    status=m.state,
                    managed=m.fingerprint is not None,
                    last_origin=m.last_origin,
                    last_change_at=m.last_change_at,
                )
            )
            for m in rows
        ]
        return {"machines": machines}

    @api.get("/events")
    async def events(request: Request) -> StreamingResponse:
        """Stream SSE de cambios de estado (AD-11, story 3.3).

        `text/event-stream` con Bearer de token de DISPOSITIVO (el middleware de
        auth ya lo exige) y SOLO tailnet+loopback (AD-6/AD-11, NFR-1): un cliente
        de la LAN física o fuera de la VPN → 403. El generador se suscribe al
        bus; sin suscriptores los eventos se omiten (v1 sin cola). Al desconectar
        el cliente, el `async with subscribe()` retira la cola sin romper la emisión.
        """
        ip = request.client.host if request.client is not None else "unknown"
        if not is_trusted_client(ip):
            logger.warning("SSE /events rechazado desde origen no loopback/tailnet: %s", ip)
            return _error(
                403, "forbidden", "el stream solo es accesible desde tailnet/loopback"
            )
        bus: EventBus = request.app.events

        async def _stream():
            async with bus.subscribe() as queue:
                # Un comentario inicial fuerza el flush de cabeceras del stream.
                yield ": connected\n\n"
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        # Latido para detectar desconexiones y mantener vivo el flujo.
                        yield ": keep-alive\n\n"
                        continue
                    payload = json.dumps(event.to_payload(), ensure_ascii=False)
                    yield f"data: {payload}\n\n"

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            # Anti-buffering: evita que proxies (nginx…) coaleszcan el stream.
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    @api.post("/machines/{machine_id}/enroll")
    async def enroll(machine_id: int, body: _EnrollBody, request: Request) -> JSONResponse:
        """Alta de máquina descubierta (AC 2.1): SSH + fingerprint + authorized_keys.

        Errores acotados (envelope AD-1): 401 password fallida, 404 inexistente,
        409 ya gestionada, 502 fallo SSH.
        """
        token = _request_token(request)
        try:
            result = await request.app.enrollment.enroll(
                machine_id, body.usuario, body.password, channel=CHANNEL_API, token=token
            )
        except EnrollmentError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)
        return JSONResponse(status_code=200, content=result)

    @api.post("/machines/{machine_id}/wake")
    async def wake(machine_id: int, request: Request) -> JSONResponse:
        """Encendido por WOL (AC 2.2): un único magic packet; siempre éxito."""
        token = _request_token(request)
        try:
            result = await request.app.wake.wake(
                machine_id, channel=CHANNEL_API, token=token
            )
        except WakeError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)
        return JSONResponse(status_code=200, content=result)

    @api.post("/machines/{machine_id}/shutdown")
    async def shutdown(machine_id: int, request: Request) -> JSONResponse:
        """Apagado por SSH (AC 2.3): verifica fingerprint; si no coincide → no_fiable."""
        token = _request_token(request)
        try:
            result = await request.app.shutdown.shutdown(
                machine_id, channel=CHANNEL_API, token=token
            )
        except ShutdownError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)
        return JSONResponse(status_code=200, content=result)

    @api.post("/scan")
    async def scan(request: Request) -> JSONResponse:
        """Escaneo forzado (FR-12, AD-3): 202 con stats; nunca 409.

        Si ya hay un escaneo en marcha (tarea viva de discovery, incluida la fase
        de upsert — deferred 1.2), responde 202 inmediato con `running: true` sin
        lanzar un segundo escaneo; si no, ejecuta el escaneo (singleflight) y
        devuelve sus estadísticas.
        """
        discovery = request.app.discovery
        if discovery.current_task is not None:
            return JSONResponse(
                status_code=202,
                content={"scan": {"running": True, "triggered": False}},
            )
        started = time.monotonic()
        discovered = await discovery.scan(origin=ORIGIN_SCAN)
        duration_ms = int((time.monotonic() - started) * 1000)
        return JSONResponse(
            status_code=202,
            content={
                "scan": {
                    "running": False,
                    "triggered": True,
                    "discovered": discovered,
                    "duration_ms": duration_ms,
                }
            },
        )

    @api.api_route("/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"])
    async def api_not_found(path: str) -> JSONResponse:
        """Rutas `/api/v1/*` no registradas → 404 con envelope uniforme (AD-1).

        Starlette responde las 404 de rutas desconocidas con un body plano
        (`{"detail": ...}`) que no cumple el contrato; el catch-all garantiza el
        envelope también en ese caso (registrar SIEMPRE la última — la precedencia
        de rutas es el orden de registro).
        """
        logger.warning("ruta desconocida bajo /api/v1: %s", path)
        return _error(404, "not_found", f"recurso no encontrado: /api/v1/{path}")

    # El MCP se monta ANTES de incluir el router de la API (que lleva el
    # catch-all `/{path:path}`): la precedencia de rutas en Starlette es el orden
    # de registro, así el Mount de `/api/v1/mcp` vence al catch-all 404.
    app.mount(MCP_PATH, mcp_sub_app)
    app.include_router(api)


app = create_app()
