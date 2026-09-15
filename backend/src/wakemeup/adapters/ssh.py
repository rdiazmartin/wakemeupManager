"""Adaptador SSH: asyncssh en proceso (FR-6, AD-5).

Wrapper delimitado al contrato del epic 2: conexión por password de un solo
uso (vive solo en memoria del proceso, jamás en disco/logs/argv/entorno),
lectura del fingerprint real de la host key (`SHA256:<base64>`, formato
OpenSSH de `asyncssh.SSHKey.get_fingerprint`), instalación idempotente de la
clave pública del BE en `authorized_keys` (append si falta) y ejecución de un
comando único sin shell libre. La password se descarta también si el alta
falla (FR-6).

Sin sshpass ni subprocesos: todo asyncssh (FR-6, AD-5). La clave privada del
BE (permisos 600, fuera de la DB según AD-7) la usa solo el servicio y no se
expone por ningún endpoint (FR-8).
"""
from __future__ import annotations

import asyncio
import logging
import re
import stat
from pathlib import Path

import asyncssh
from asyncssh.sftp import SFTPAttrs

from wakemeup.config import SshSettings

logger = logging.getLogger(__name__)

_FINGERPRINT_PREFIX = "SHA256:"
_CONNECT_TIMEOUT = 10
_RUN_TIMEOUT = 30


class SshError(Exception):
    """Fallo genérico del adaptador SSH (errores claros para el API, FR-9)."""


class AuthFailedError(SshError):
    """Autenticación rechazada (password incorrecta): 401 en el alta (AC 2.1)."""


def _public_key_line(public_key: bytes | str) -> str:
    """Normaliza una clave pública a la línea OpenSSH en str, sin salto final.

    `asyncssh.SSHKey.export_public_key` devuelve bytes (versiones actuales) o
    str según la versión; la comparación/escritura en `authorized_keys` es
    textual, así que aquí se resuelve la ambigüedad en un único punto
    (regresión del alta real: `bytes.rstrip("\n")` → TypeError → 500).
    """
    if isinstance(public_key, bytes):
        return public_key.decode().rstrip("\n")
    return public_key.rstrip("\n")


class Ssh:
    """Primitivas SSH sobre asyncssh (una conexión por acción)."""

    def __init__(self, settings: SshSettings) -> None:
        self._settings = settings
        self._client_key: asyncssh.SSHKey | None = None
        self._own_public_key: str | None = None

    async def _resolve_remote_home(self, sftp: asyncssh.SFTPClient) -> str:
        """Home remoto para `authorized_keys` (config `[ssh] remote_home`).

        - Ruta absoluta configurada → se usa tal cual (p. ej. `/home/maria`).
        - Vacía "" → se resuelve el home del usuario conectado con
          `sftp.realpath(".")` (SFTP expande el directorio de trabajo como el
          home del usuario en la sesión de inicio, no como `~`).
        - "~" explícito → error claro (SFTP NO expande el tilde; sería un
          `authorized_keys` ilegible para OpenSSH).
        """
        configured = (self._settings.remote_home or "").strip()
        if configured == "~":
            raise SshError(
                "[ssh] remote_home='~' no es válido: SFTP no expande el tilde; "
                "configura la ruta absoluta del home remoto (p. ej. /home/maria) "
                "o deja el valor vacío para resolver el home del usuario conectado"
            )
        if configured:
            return configured.rstrip("/")
        real = await sftp.realpath(".") if hasattr(sftp, "realpath") else None
        if real is None:
            raise SshError(
                "[ssh] remote_home vacío y el servidor no resolvió el home; "
                "configura la ruta absoluta (p. ej. /home/maria)"
            )
        return str(real).rstrip("/")

    @property
    def authorized_keys_path(self) -> str:
        """Ruta remota de `authorized_keys` (dirigible en config, AD-5).

        Solo configuración estática (para diagnóstico/tests): la instalación
        resuelve el home real vía SFTP (`_resolve_remote_home`).
        """
        home = (self._settings.remote_home or "").strip()
        return f"{home}/.ssh/authorized_keys" if home and home != "~" else ".ssh/authorized_keys"

    async def _load_client_keys(self) -> asyncssh.SSHKey:
        """Carga el par Ed25519 privado (600) en memoria, una sola vez por proceso.

        Lee la clave privada del fichero (se advierte si no tiene permisos 600,
        AD-7) y conserva el objeto [asyncssh.SSHKey] como credencial de cliente
        (`client_keys` acepta SSHKey objects; los bytes de export_public_key
        NO son una clave privada válida y KeyImportError en la conexión).
        """
        if self._client_key is not None:
            return self._client_key
        key_path = Path(self._settings.key_path)
        if not key_path.exists():
            msg = (
                f"par de claves SSH del BE no encontrado en {key_path} "
                "(configurar [ssh] key_path o generar `ssh-keygen -t ed25519`); "
                "el alta con password sigue disponible, el control posterior no"
            )
            logger.error(msg)
            raise SshError(msg)
        st = key_path.stat()
        if stat.S_IMODE(st.st_mode) != 0o600:
            logger.warning(
                "clave privada %s con permisos %o (esperado 600)",
                key_path, st.st_mode,
            )
        try:
            private_key = asyncssh.read_private_key(str(key_path))
        except (OSError, ValueError, asyncssh.KeyImportError) as exc:
            raise SshError(f"par de claves SSH ilegible en {key_path}: {exc}") from exc
        self._own_public_key = _public_key_line(private_key.export_public_key())
        self._client_key = private_key
        return self._client_key

    async def connect(self, host: str, user: str, password: str) -> asyncssh.SSHClientConnection:
        """Abre una conexión por password (una sola vez en proceso, FR-6).

        La password solo existe en el argumento/stack del proceso; nunca en
        disco, logs, argv ni entorno. El fingerprint de host NO se valida aquí:
        el alta lo captura de ESTA misma conexión antes de confiar (sin MITM
        posible entre lectura e instalación, nota de diseño 2.1). Fallo de
        autenticación → [AuthFailedError]; cualquier otro → [SshError].
        """
        try:
            return await asyncssh.connect(
                host,
                username=user,
                password=password,
                known_hosts=None,  # AD-9: nunca confiar en known_hosts
                client_keys=[await self._load_client_keys()],
                connect_timeout=_CONNECT_TIMEOUT,
            )
        except asyncssh.PermissionDenied as exc:
            logger.info("autenticación rechazada en %s (alta)", host)
            raise AuthFailedError("credenciales incorrectas") from exc
        except (OSError, asyncssh.Error, asyncio.TimeoutError) as exc:
            raise SshError(f"no se pudo conectar a {host}: {exc}") from exc

    async def connect_key(self, host: str, user: str) -> asyncssh.SSHClientConnection:
        """Abre una conexión con el par de claves del BE (apagado, 2.3).

        Sin password: la identidad es el fingerprint `SHA256:base64` fijado en
        el alta (AD-2/AD-9), que el llamador verifica ANTES de ejecutar el
        comando.
        """
        try:
            return await asyncssh.connect(
                host,
                username=user,
                known_hosts=None,  # AD-9: nunca confiar en known_hosts
                client_keys=[await self._load_client_keys()],
                connect_timeout=_CONNECT_TIMEOUT,
            )
        except asyncssh.PermissionDenied as exc:
            logger.info("autenticación por clave rechazada en %s", host)
            raise SshError("clave del BE no aceptada en el host remoto") from exc
        except (OSError, asyncssh.Error, asyncio.TimeoutError) as exc:
            raise SshError(f"no se pudo conectar a {host}: {exc}") from exc

    async def read_host_fingerprint(self, connection: asyncssh.SSHClientConnection) -> str:
        """Fingerprint `SHA256:<base64>` de la host key presentada en la conexión.

        Equivale al `SHA256:` de `ssh-keygen -lf` (AD-2). Se lee ANTES de
        instalar la clave pública: el fingerprint y la instalación comparten
        conexión, así que no hay ventana de MITM (nota de diseño 2.1).
        """
        key = connection.get_server_host_key()
        if key is None:
            raise SshError("el servidor no presentó host key; aborte el alta")
        fingerprint = key.get_fingerprint("sha256")
        if not fingerprint.startswith(_FINGERPRINT_PREFIX):
            raise SshError(f"fingerprint inesperado del servidor: {fingerprint!r}")
        return fingerprint

    async def install_authorized_key(self, connection: asyncssh.SSHClientConnection) -> None:
        """Instala la clave pública del BE en `authorized_keys` (idempotente).

        SFTP: resuelve el home remoto (config o el del usuario vía realpath),
        crea `~/.ssh` con 700 si falta, append si la clave aún no está y fija
        `authorized_keys` a 0600 (una umask del servidor NO debe dejar un modo
        que OpenSSH rechace luego en el apagado por clave, AD-9).
        """
        public_key = self._own_public_key or _public_key_line(
            (await self._load_client_keys()).export_public_key()
        )
        async with connection.start_sftp_client() as sftp:
            try:
                home = await self._resolve_remote_home(sftp)
                await self._ensure_ssh_dir(sftp, home)
                path = f"{home}/.ssh/authorized_keys"
                existing = ""
                try:
                    async with sftp.open(path, "r") as fh:
                        existing = await fh.read()
                except (asyncssh.SFTPNoSuchFile, OSError):
                    existing = ""
                # `export_public_key` puede devolver bytes o str según versión:
                # se normaliza a str OpenSSH sin salto final (regresión del alta
                # real: `bytes.rstrip("\n")` → TypeError → 500).
                existing = existing.decode() if isinstance(existing, bytes) else existing
                key_line = _public_key_line(public_key)
                lines = [ln for ln in existing.splitlines() if ln.strip()]
                seen = any(ln == key_line for ln in lines)
                if not seen:
                    lines.append(key_line)
                    lines.append("")
                    data = "\n".join(lines)
                    async with sftp.open(path, "w") as fh:
                        await fh.write(data)
                    logger.info("clave pública instalada en %s", path)
                else:
                    logger.info("clave pública ya presente en %s; sin cambios", path)
                # Permisos 0600 SIEMPRE (create o rewrite): un authorized_keys
                # con modo grupal/abierto hace que sshd rechace la clave.
                await sftp.setstat(path, SFTPAttrs(permissions=0o600))
            except asyncssh.SFTPError as exc:
                raise SshError(
                    f"no se pudo instalar authorized_keys en {home}: {exc}"
                ) from exc

    async def run_command(self, connection: asyncssh.SSHClientConnection, command: str) -> str:
        """Ejecuta un comando único y espera su salida; sin shell libre.

        Devuelve el stdout si el retorno es cero; si no, [SshError] con el
        motivo (p. ej. "sudo" para NOPASSWD no configurado, FR-9/AC 2.3).
        """
        try:
            result = await asyncio.wait_for(
                connection.run(command, check=False), timeout=_RUN_TIMEOUT
            )
        except (asyncssh.Error, asyncio.TimeoutError) as exc:
            raise SshError(f"no se pudo ejecutar el comando en el host remoto: {exc}") from exc
        if result.exit_status != 0:
            stderr = (result.stderr or "").strip()
            # Exit 255 es también un fallo remoto genérico: solo se diagnostica
            # "sudo/NOPASSWD" cuando el stderr/msg nombra sudo o privilegios
            # (FR-9: error claro sin inventar la causa).
            if result.exit_status == 255 and re.search(
                r"(?i)\bsudo\b|privileg|nopasswd", (stderr or "")[:200]
            ):
                raise SshError("sudo: el usuario remoto no tiene privilegios sudo (NOPASSWD)")
            raise SshError(
                f"el comando remoto falló (exit {result.exit_status})"
                + (f": {stderr[:200]}" if stderr else "")
            )
        return (result.stdout or "").strip()

    async def _ensure_ssh_dir(self, sftp: asyncssh.SFTPClient, home: str) -> None:
        """Crea `~/.ssh` con permisos 700 si no existe (SFTP mkdir recursivo)."""
        ssh_dir = f"{home}/.ssh"
        try:
            try:
                await sftp.stat(ssh_dir)
                return
            except (asyncssh.SFTPNoSuchFile, OSError):
                pass
            try:
                await sftp.mkdir(ssh_dir)
                # Permisos 0700 del dir: sin él sshd tampoco acepta la clave.
                await sftp.setstat(ssh_dir, SFTPAttrs(permissions=0o700))
                logger.info("directorio %s creado (700)", ssh_dir)
            except asyncssh.SFTPNoSuchFile:
                # Home remoto inexistente o SFTP sin mkdir: lo reporta el alta
                raise SshError(f"no se pudo crear {ssh_dir}: home remoto inexistente")
        except asyncssh.SFTPError as exc:
            raise SshError(f"no se pudo preparar {ssh_dir}: {exc}") from exc

    async def close(self, connection: asyncssh.SSHClientConnection | None) -> None:
        """Cierra la conexión si sigue abierta (limpieza en todos los caminos)."""
        if connection is not None and not connection.is_closed():
            connection.close()
            try:
                await asyncio.wait_for(connection.wait_closed(), timeout=5)
            except (asyncio.TimeoutError, asyncssh.Error):
                logger.warning("cierre de conexión SSH con timeout")
