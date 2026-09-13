"""App FastAPI del BE de wakemeupManager (AD-1).

Prefijo `/api/v1` con healthcheck exento (decisión 1.2), auth Bearer por token
de dispositivo con backoff (FR-10/AD-6), inventario (AD-10) y escaneo forzado
(AD-3). El lifespan conecta los loops periódicos de discovery y status al
ciclo de vida de la app, inicializa la DB y los detiene al apagar.

Los servicios se exponen como atributos de `app` (sustituibles en tests sin
abrir la DB real); el lifespan los construye desde la configuración.
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from wakemeup import __version__
from wakemeup.adapters.db import Db
from wakemeup.adapters.net import Net
from wakemeup.config import Settings
from wakemeup.core.models import MachineDTO
from wakemeup.services.auth import AuthService, sha256_hex_lower
from wakemeup.services.discovery import DiscoveryService
from wakemeup.services.status import StatusService

logger = logging.getLogger(__name__)


def _build_services() -> tuple[Db, Net, DiscoveryService, StatusService, AuthService]:
    """Grafo de servicios desde la configuración (sin abrir la DB todavía)."""
    settings = Settings()
    db = Db(settings.db.path)
    net = Net()
    return (
        db,
        net,
        DiscoveryService(net=net, db=db, scan=settings.scan),
        StatusService(net=net, db=db, scan=settings.scan),
        AuthService(db=db, settings=settings.auth),
    )


_db, _net, _discovery, _status, _auth = _build_services()


@asynccontextmanager
async def _lifespan(_: FastAPI):
    """Arranque/apagado de la app: DB + loops periódicos (FR-2/FR-3).

    Consume el grafo expuesto en `app.*` (sustituible en tests); al apagar se
    cancelan los loops periódicos y se espera a su final (incl. la iteración
    en curso) ANTES de cerrar la DB.
    """
    await app.db.init_db()
    t_disc = app.discovery.periodic_task()
    t_status = app.status.check_cycle()
    try:
        yield
    finally:
        app.discovery.stop()
        app.status.stop()
        for task in (t_disc, t_status):
            try:
                await task
            except (asyncio.CancelledError, Exception):  # el loop puede fallar al cancelarse
                pass
        await app.db.close()


app = FastAPI(title="wakemeup-backend", version=__version__, lifespan=_lifespan)
app.db = _db
app.net = _net
app.discovery = _discovery
app.status = _status
app.auth = _auth

api = APIRouter(prefix="/api/v1")

_ERROR_CODES = {
    400: "bad_request",
    401: "unauthorized",
    404: "not_found",
    409: "conflict",
    422: "validation_error",
    429: "too_many_requests",
}


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    """Envelope uniforme de error (AD-1): `{"error": {"code", "message"}}`."""
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


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
    """Bearer token obligatorio en `/api/v1/*`, salvo exenciones (FR-10, AD-6)."""
    if not request.url.path.startswith("/api/v1"):
        return await call_next(request)
    if AuthService.is_exempt(request.method, request.url.path):
        return await call_next(request)

    header = request.headers.get("authorization", "")
    token = header[7:] if header.lower().startswith("bearer ") else None
    digest = sha256_hex_lower(token) if token is not None else None
    ip = request.client.host if request.client is not None else "unknown"

    if app.auth.backoff.is_blocked(ip) or (
        digest is not None and app.auth.backoff.is_blocked(digest)
    ):
        return _error(
            429,
            "too_many_requests",
            "demasiados intentos de autenticación; reintente más tarde",
        )
    if not await app.auth.authenticate(token, ip):
        return _error(
            401, "unauthorized", "token de dispositivo inválido o ausente"
        )
    return await call_next(request)


@api.get("/status")
async def status() -> dict:
    """Healthcheck (exento de auth): estado/versión + interfaz WOL (FR-5 parcial).

    Sin interfaz Ethernet emisora, el BE sigue vivo pero reporta `warning`
    (WOL no fiable vía WiFi); el healthcheck nunca bloquea.
    """
    wl = await app.net.wol_interface()
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
async def machines() -> dict:
    """Listado con DTO AD-10 completo; `managed=false` hasta el alta (Epic 2)."""
    rows = await app.status.list_with_status()
    machines = [
        asdict(
            MachineDTO(
                id=m.id,
                name=m.hostname or m.ip,
                ip=m.ip,
                mac=m.mac,
                hostname=m.hostname,
                status=m.state,
                managed=False,
            )
        )
        for m in rows
    ]
    return {"machines": machines}


@api.post("/scan")
async def scan() -> JSONResponse:
    """Escaneo forzado (FR-12, AD-3): 202 con stats; nunca 409.

    Si ya hay un escaneo en marcha (tarea viva de discovery, incluida la fase
    de upsert — deferred 1.2), responde 202 inmediato con `running: true` sin
    lanzar un segundo escaneo; si no, ejecuta el escaneo (singleflight) y
    devuelve sus estadísticas.
    """
    if app.discovery.current_task is not None:
        return JSONResponse(
            status_code=202,
            content={"scan": {"running": True, "triggered": False}},
        )
    started = time.monotonic()
    discovered = await app.discovery.scan()
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


app.include_router(api)
