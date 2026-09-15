"""Servicio de autenticación por token de dispositivo (FR-10, AD-6).

El BE guarda solo `SHA-256(hex-lower)` de los 32 B aleatorios; cada petición
hashea la misma representación y compara contra la BD. Los fallos de
autenticación aplican backoff por token y por IP fuente: 5 fallos en 5 minutos
bloquean la fuente durante 15 minutos (contadores en memoria del proceso,
se resetean al reiniciar — AD-6). El healthcheck (`GET /api/v1/status`) está
exento por utilidad operativa (decisión 1.2).
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time

from wakemeup.adapters.db import Db
from wakemeup.config import AuthSettings

logger = logging.getLogger(__name__)

# Rutas exentas de autenticación (decision 1.2 / deferred 1.1): el healthcheck
# es anónimo por utilidad operativa (systemd Restart/healthcheck). GET y HEAD
# comparten ruta en Starlette (HEAD → GET); el prefijo ignora trailing slash.
_AUTH_EXEMPT_PREFIXES: tuple[tuple[str, str], ...] = (("GET", "/api/v1/status"),)

_EXEMPT_GET_PATHS = {p for m, p in _AUTH_EXEMPT_PREFIXES if m == "GET"}


def sha256_hex_lower(hex_token: str) -> str:
    """Hash de la pre-imagen canónica: la cadena hex en minúsculas (AD-6)."""
    return hashlib.sha256(hex_token.encode("ascii")).hexdigest().lower()


def new_device_token() -> str:
    """Token de dispositivo: 32 B aleatorios en representación hex-lower."""
    return secrets.token_bytes(32).hex()


class AuthBackoff:
    """Ventana deslizante de fallos por clave (token/IP), en memoria (AD-6)."""

    def __init__(self, settings: AuthSettings) -> None:
        self._max_failures = settings.max_failures
        self._window = settings.window_seconds
        self._block = settings.block_seconds
        self._failures: dict[str, list[float]] = {}
        self._blocked_until: dict[str, float] = {}

    def is_blocked(self, key: str) -> bool:
        now = time.monotonic()
        until = self._blocked_until.get(key)
        if until is not None and now < until:
            return True
        if until is not None:
            self._blocked_until.pop(key, None)
            self._failures.pop(key, None)
        return False

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        stamps = [t for t in self._failures.get(key, []) if now - t <= self._window]
        stamps.append(now)
        self._failures[key] = stamps
        if len(stamps) >= self._max_failures:
            self._blocked_until[key] = now + self._block
            self._failures.pop(key, None)
            # FR-11/AD-6: NUNCA se loguea la clave (puede ser el token plano),
            # solo el bloqueo y su duración.
            logger.warning("autenticación bloqueada (429) durante %d s", self._block)

    def success(self, key: str) -> None:
        self._failures.pop(key, None)

    def reset(self) -> None:
        """Limpia contadores y bloqueos (tests y rotación manual)."""
        self._failures.clear()
        self._blocked_until.clear()


class AuthService:
    """Verificación de tokens de dispositivo con backoff (FR-10, AD-6)."""

    def __init__(self, db: Db, settings: AuthSettings) -> None:
        self._db = db
        self.backoff = AuthBackoff(settings)

    @staticmethod
    def is_exempt(method: str, path: str) -> bool:
        """Exención del healthcheck: GET y HEAD (misma ruta en Starlette),
        ignorando trailing slash; el resto de rutas requieren token."""
        if method in ("GET", "HEAD"):
            return path.rstrip("/") in _EXEMPT_GET_PATHS
        return False

    async def authenticate(self, token: str | None, ip: str, kind: str = "device") -> bool:
        """¿Es válido este token (`kind`) desde esta IP?

        No distingue token ausente vs inválido (mismo 401) para no oracular
        la existencia de tokens (FR-10). Los contadores de backoff usan la IP
        y el HASH del token (nunca el token plano: FR-11/AD-6).

        `kind` (epic 3, FR-10b): el middleware de dispositivo exige
        `device` (default, no rompe llamadas) y el auth del MCP exige `mcp`;
        un token de un tipo no vale en la superficie del otro.
        """
        digest = sha256_hex_lower(token) if token is not None else None
        blocked = self.backoff.is_blocked(ip) or (
            digest is not None and self.backoff.is_blocked(digest)
        )
        if blocked:
            return False
        if digest is None:
            self.backoff.record_failure(ip)
            return False
        if not await self._db.token_exists(digest, kind):
            logger.info("token %s inválido o revocado (origen %s)", kind, ip)
            self.backoff.record_failure(ip)
            self.backoff.record_failure(digest)
            return False
        self.backoff.success(ip)
        self.backoff.success(digest)
        return True
