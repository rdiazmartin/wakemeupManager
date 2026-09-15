"""Instalador idempotente del BE como servicio systemd (story 4.1, epic 4).

Diseñado para ejecutarse como ROOT en el host de despliegue (nunca en la
máquina de desarrollo). Hace, en orden y de forma repetible:

1. Detecta AUTO la IP tailnet (`tailscale ip -4`) y el DNSName/MagicDNS
   (`tailscale status --json`); sin `tailscale` avisa y deja solo loopback.
2. Crea el usuario de sistema dedicado `wakemeup` (si no existe).
3. Prepara `/var/lib/wakemeup/` y `/etc/wakemeup/`.
4. Genera el par Ed25519 (privada `600` en directorio `700`) si no existe.
5. Escribe la config desde plantilla SOLO si no existe (no la pisa jamás).
6. Sincroniza las dependencias con `uv sync --frozen` (reproduce `uv.lock`).
7. Escribe la unidad systemd (solo si cambia), `daemon-reload` y `enable --now`.
8. Healthcheck `GET /api/v1/status` (200 = vivo); si falla, rc≠0.

Todas las operaciones de sistema pasan por un `Runner` inyectable: los tests
usan un doble y un `tmp_path` sin tocar el host real. Nunca se registran
passwords, claves ni contenido de `authorized_keys` (FR-11): solo el estado de
los pasos.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Sequence
from typing import Protocol

logger = logging.getLogger("wakemeup.install")

DEFAULT_USER = "wakemeup"
DEFAULT_APP_DIR = Path("/opt/wakemeup/backend")
DEFAULT_CONFIG_PATH = Path("/etc/wakemeup/config.toml")
DEFAULT_DATA_DIR = Path("/var/lib/wakemeup")
DEFAULT_UNIT_PATH = Path("/etc/systemd/system/wakemeup.service")
DEFAULT_PORT = 8080
DEFAULT_RETRY_SECONDS = 30.0
_HEALTH_TIMEOUT_SECONDS = 30.0
_HEALTH_INTERVAL_SECONDS = 1.0

# La tailnet de Tailscale vive en el rango CGNAT 100.64.0.0/10.
_TAILNET_CGNAT = ipaddress.ip_network("100.64.0.0/10")


class InstallError(RuntimeError):
    """Fallo irrecuperable del instalador (mensaje legible, rc≠0)."""


class Runner(Protocol):
    """Ejecuta comandos del sistema (inyectable en tests)."""

    def run(
        self, argv: Sequence[str], *, check: bool = True, cwd: str | None = None
    ) -> subprocess.CompletedProcess: ...


class SubprocessRunner:
    """`Runner` real: `subprocess.run` sin shell, capturando stdout/stderr."""

    def run(
        self, argv: Sequence[str], *, check: bool = True, cwd: str | None = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            list(argv), check=check, cwd=cwd, capture_output=True, text=True
        )


@dataclass(frozen=True)
class InstallPaths:
    """Rutas/identidad del despliegue (sobreescribibles en tests)."""

    app_dir: Path = DEFAULT_APP_DIR
    user: str = DEFAULT_USER
    data_dir: Path = DEFAULT_DATA_DIR
    config_path: Path = DEFAULT_CONFIG_PATH
    unit_path: Path = DEFAULT_UNIT_PATH
    port: int = DEFAULT_PORT
    bind_retry_seconds: float = DEFAULT_RETRY_SECONDS
    uv_path: str = ""

    @property
    def venv_python(self) -> Path:
        return self.app_dir / ".venv" / "bin" / "python"

    @property
    def key_dir(self) -> Path:
        return self.data_dir / "keys"

    @property
    def key_path(self) -> Path:
        return self.key_dir / "id_ed25519"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "wakemeup.db"


CONFIG_TEMPLATE = """\
# wakemeupManager BE — configuración de despliegue.
# Generada por `wakemeup-install`; el instalador NO la sobreescribe en
# re-ejecuciones (edítala a mano si necesitas cambios).

[scan]
range = "192.168.1.0/24"
interval_seconds = 600
ttl_seconds = 60

[db]
# SQLite en el directorio de datos del servicio (WorkingDirectory).
path = "{db_path}"

[api]
# Solo tailnet + loopback (FR-9/AD-6): nunca 0.0.0.0 ni la LAN física.
bind_hosts = [{bind_hosts}]
port = {port}
prefix = "/api/v1"
# Reintento del bind de la tailnet si aún no está asignada (AD: arranque
# tolerante a la tailnet tardía).
bind_retry_seconds = {bind_retry_seconds}
# Nombres extra permitidos en el `Host` header del MCP (MagicDNS).
extra_allowed_hosts = [{extra_hosts}]

[shutdown]
command = "sudo -n systemctl poweroff"

[ssh]
# Par Ed25519 del BE: privada 600, usada por el servicio no-root.
key_path = "{key_path}"
# Vacío → el alta resuelve el home remoto vía SFTP.
remote_home = ""

[auth]
max_failures = 5
window_seconds = 300
block_seconds = 900
"""

UNIT_TEMPLATE = """\
[Unit]
Description=wakemeupManager backend - control de máquinas de la red via WOL/SSH
Wants=network-online.target
After=network-online.target tailscaled.service

[Service]
User={user}
Group={user}
WorkingDirectory={data_dir}
Environment=WAKEMEUP_DB__PATH={db_path}
# Intérprete del venv gestionado por `uv` (nunca el Python de sistema 3.11).
ExecStart={venv_python} -m wakemeup.server
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths={data_dir}

[Install]
WantedBy=multi-user.target
"""


def _quote_toml_list(values: Sequence[str]) -> str:
    return ", ".join(f'"{v}"' for v in values)


def render_config(
    *,
    db_path: Path,
    key_path: Path,
    bind_hosts: Sequence[str],
    extra_hosts: Sequence[str],
    port: int,
    bind_retry_seconds: float,
) -> str:
    """Renderiza la config del despliegue con los valores detectados."""
    return CONFIG_TEMPLATE.format(
        db_path=db_path,
        key_path=key_path,
        bind_hosts=_quote_toml_list(bind_hosts),
        extra_hosts=_quote_toml_list(extra_hosts),
        port=port,
        bind_retry_seconds=bind_retry_seconds,
    )


def render_unit(paths: InstallPaths) -> str:
    """Renderiza la unidad systemd con las rutas/el intérprete del venv."""
    return UNIT_TEMPLATE.format(
        user=paths.user,
        data_dir=paths.data_dir,
        db_path=paths.db_path,
        venv_python=paths.venv_python,
    )


class Installer:
    """Orquesta el despliegue idempotente paso a paso."""

    def __init__(
        self,
        runner: Runner,
        paths: InstallPaths | None = None,
        *,
        http_get: Callable[[str, float], int] | None = None,
    ) -> None:
        self._runner = runner
        self._paths = paths or InstallPaths()
        # `http_get(url, timeout)` inyectable; default urllib.
        self._http_get = http_get or _urllib_status

    # ---- pasos -----------------------------------------------------------

    def detect_tailnet(self) -> tuple[str | None, str | None]:
        """Devuelve `(ip_tailnet, dnsname)` o `(None, None)` sin tailscale.

        `bind_sockets`/el runner toleran la ausencia; aquí solo se decide la
        config. Un `tailscale` presente pero sin IP aún → `(None, dnsname)`.
        """
        ip = self._tailscale_ipv4()
        dns = self._tailscale_dnsname()
        if ip is None and dns is None:
            logger.warning(
                "tailscale no disponible: la config queda solo-loopback; "
                "completa `[api] bind_hosts`/`extra_allowed_hosts` a mano",
            )
        return ip, dns

    def _tailscale_ipv4(self) -> str | None:
        try:
            proc = self._runner.run(["tailscale", "ip", "-4"], check=False)
        except FileNotFoundError:
            logger.warning("binario `tailscale` no encontrado; sin IP tailnet")
            return None
        if proc.returncode != 0:
            logger.warning("`tailscale ip -4` falló (rc=%s); sin IP tailnet", proc.returncode)
            return None
        for line in (proc.stdout or "").splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            try:
                addr = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if addr in _TAILNET_CGNAT:
                return str(addr)
        logger.warning("`tailscale ip -4` no devolvió una IP de la tailnet; se ignora")
        return None

    def _tailscale_dnsname(self) -> str | None:
        try:
            proc = self._runner.run(["tailscale", "status", "--json"], check=False)
        except FileNotFoundError:
            return None
        if proc.returncode != 0:
            logger.warning("`tailscale status --json` falló (rc=%s)", proc.returncode)
            return None
        try:
            data = json.loads(proc.stdout or "{}")
        except (json.JSONDecodeError, TypeError):
            logger.warning("`tailscale status --json` no devolvió JSON válido")
            return None
        dns = (data.get("Self") or {}).get("DNSName")
        if not isinstance(dns, str) or not dns:
            return None
        return dns.rstrip(".")

    def ensure_user(self) -> bool:
        """Crea el usuario de sistema si falta; `False` si ya existía."""
        if self._user_exists():
            logger.info("usuario %s ya existe", self._paths.user)
            return False
        logger.info("creando usuario de sistema %s", self._paths.user)
        self._runner.run(
            [
                "useradd",
                "--system",
                "--create-home",
                "--shell",
                "/usr/sbin/nologin",
                self._paths.user,
            ]
        )
        return True

    def _user_exists(self) -> bool:
        try:
            proc = self._runner.run(["id", "-u", self._paths.user], check=False)
        except FileNotFoundError:
            return False
        return proc.returncode == 0

    def ensure_dirs(self) -> None:
        """Crea directorios de datos/config con permisos correctos."""
        self._paths.data_dir.mkdir(parents=True, exist_ok=True)
        self._paths.key_dir.mkdir(parents=True, exist_ok=True)
        self._paths.config_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self._paths.key_dir, 0o700)
        self._chown(self._paths.data_dir)
        self._chown(self._paths.key_dir)

    def ensure_keys(self) -> bool:
        """Genera el par Ed25519 si falta; `False` si ya existía (idempotente)."""
        if self._paths.key_path.exists():
            logger.info("par de claves ya existe; no se regenera")
            self._apply_key_perms()
            return False
        logger.info("generando par Ed25519 en %s", self._paths.key_path)
        self._runner.run(
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-N",
                "",
                "-C",
                "wakemeup BE",
                "-f",
                str(self._paths.key_path),
            ]
        )
        self._apply_key_perms()
        return True

    def _apply_key_perms(self) -> None:
        if self._paths.key_path.exists():
            os.chmod(self._paths.key_path, 0o600)
        pub = Path(str(self._paths.key_path) + ".pub")
        if pub.exists():
            os.chmod(pub, 0o600)
        for path in (self._paths.key_path, pub):
            if path.exists():
                self._chown(path)

    def write_config(self, bind_hosts: Sequence[str], extra_hosts: Sequence[str]) -> bool:
        """Escribe la config si NO existe; `False` si se conserva (no se pisa)."""
        if self._paths.config_path.exists():
            logger.info("config existente conservada: %s", self._paths.config_path)
            self._apply_config_perms()
            return False
        content = render_config(
            db_path=self._paths.db_path,
            key_path=self._paths.key_path,
            bind_hosts=bind_hosts,
            extra_hosts=extra_hosts,
            port=self._paths.port,
            bind_retry_seconds=self._paths.bind_retry_seconds,
        )
        self._paths.config_path.write_text(content)
        self._apply_config_perms()
        logger.info("config escrita: %s", self._paths.config_path)
        return True

    def _apply_config_perms(self) -> None:
        os.chmod(self._paths.config_path, 0o600)
        self._chown(self._paths.config_path)

    def sync_dependencies(self) -> None:
        """`uv sync --frozen`: reproduce exactamente el entorno de `uv.lock`."""
        uv = self._resolve_uv()
        logger.info("sincronizando dependencias con `%s sync --frozen`", uv)
        self._runner.run(
            [uv, "sync", "--frozen", "--project", str(self._paths.app_dir)]
        )

    def _resolve_uv(self) -> str:
        if self._paths.uv_path:
            return self._paths.uv_path
        for candidate in (
            Path("/usr/local/bin/uv"),
            Path.home() / ".local" / "bin" / "uv",
        ):
            if candidate.exists():
                return str(candidate)
        found = shutil.which("uv")
        if found:
            return found
        raise InstallError(
            "`uv` no encontrado: instálalo (https://docs.astral.sh/uv/) o pasa --uv-path"
        )

    def write_unit(self) -> bool:
        """Escribe la unidad si cambia; `False` si es idéntica (no la duplica)."""
        content = render_unit(self._paths)
        if self._paths.unit_path.exists():
            current = self._paths.unit_path.read_text()
            if current == content:
                logger.info("unidad sin cambios: %s", self._paths.unit_path)
                return False
        self._paths.unit_path.parent.mkdir(parents=True, exist_ok=True)
        self._paths.unit_path.write_text(content)
        os.chmod(self._paths.unit_path, 0o644)
        logger.info("unidad escrita: %s", self._paths.unit_path)
        return True

    def enable_service(self) -> None:
        """`daemon-reload` + `enable --now` (idempotente)."""
        self._runner.run(["systemctl", "daemon-reload"])
        self._runner.run(["systemctl", "enable", "--now", "wakemeup.service"])

    def healthcheck(
        self,
        *,
        timeout: float = _HEALTH_TIMEOUT_SECONDS,
        interval: float = _HEALTH_INTERVAL_SECONDS,
    ) -> bool:
        """Poll `GET /api/v1/status` hasta 200 o agotar `timeout`."""
        url = f"http://127.0.0.1:{self._paths.port}/api/v1/status"
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            try:
                status = self._http_get(url, 5.0)
            except Exception as exc:  # conexión rechazada, timeout, etc.
                logger.info("healthcheck aún no responde (%s)", exc)
                status = None
            if status == 200:
                logger.info("healthcheck OK (200) en %s", url)
                return True
            if time.monotonic() >= deadline:
                logger.error("healthcheck fallido: %s no devolvió 200", url)
                return False
            time.sleep(interval)

    def _chown(self, path: Path) -> None:
        try:
            self._runner.run(["chown", "-R", f"{self._paths.user}:{self._paths.user}", str(path)])
        except FileNotFoundError:  # host sin chown (tests): se ignora
            logger.warning("`chown` no disponible; se omite para %s", path)

    # ---- orquestación ----------------------------------------------------

    def run(self) -> int:
        """Ejecuta el despliegue completo; `0` ok, `1` fallo (rc≠0)."""
        tailnet_ip, dnsname = self.detect_tailnet()
        bind_hosts = [tailnet_ip, "127.0.0.1"] if tailnet_ip else ["127.0.0.1"]
        extra_hosts = [dnsname] if dnsname else []

        self.ensure_user()
        self.ensure_dirs()
        self.ensure_keys()
        self.write_config(bind_hosts, extra_hosts)
        try:
            self.sync_dependencies()
        except InstallError as exc:
            logger.error("%s", exc)
            return 1
        self.write_unit()
        self.enable_service()
        if not self.healthcheck():
            logger.error("el servicio no superó el healthcheck; revisa `journalctl -u wakemeup`")
            return 1
        logger.info("despliegue completado; bind_hosts=%s extra_allowed_hosts=%s", bind_hosts, extra_hosts)
        return 0


def _urllib_status(url: str, timeout: float) -> int:
    """`GET` con urllib; devuelve el status HTTP (200 = vivo)."""
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def _require_root() -> None:
    if os.geteuid() != 0:
        raise InstallError(
            "el instalador debe ejecutarse como root (usa `sudo deploy/install.sh`)"
        )


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        prog="wakemeup-install", description="Instalador idempotente del BE (systemd)"
    )
    parser.add_argument("--app-dir", type=Path, default=DEFAULT_APP_DIR)
    parser.add_argument("--user", default=DEFAULT_USER)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--unit-path", type=Path, default=DEFAULT_UNIT_PATH)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--uv-path", default="", help="ruta explícita del binario `uv` (autodetecta si vacío)"
    )
    parser.add_argument(
        "--skip-root-check",
        action="store_true",
        help=argparse.SUPPRESS,  # solo para tests/integraciones controladas
    )
    args = parser.parse_args(argv)

    paths = InstallPaths(
        app_dir=args.app_dir,
        user=args.user,
        data_dir=args.data_dir,
        config_path=args.config_path,
        unit_path=args.unit_path,
        port=args.port,
        uv_path=args.uv_path,
    )
    if not args.skip_root_check:
        try:
            _require_root()
        except InstallError as exc:
            logger.error("%s", exc)
            sys.exit(1)

    installer = Installer(SubprocessRunner(), paths)
    try:
        sys.exit(installer.run())
    except InstallError as exc:
        logger.error("fallo del instalador: %s", exc)
        sys.exit(1)
    except subprocess.CalledProcessError as exc:
        logger.error("comando fallido (%s): %s", exc.returncode, exc.cmd)
        sys.exit(1)


if __name__ == "__main__":
    main()
