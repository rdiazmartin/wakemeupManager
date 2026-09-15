"""Configuración del BE (convención del spine): `config.toml` + override env.

Sección `[scan]` (1.2), `[db]` y `[auth]` (1.4), `[ssh]` y `[shutdown]` (2.1/2.3).
Sin fichero, se usan los defaults de código (escaneo cada 600 s, TTL 60 s,
rango 192.168.1.0/24, DB `wakemeup.db` en CWD, backoff 5 fallos/5 min →
429/15 min, par de claves Ed25519 del BE, comando de apagado systemd).
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

DEFAULT_CONFIG_PATH = Path("config.toml")


def default_config_path() -> Path:
    """Ruta del config.toml: `backend/config/config.toml` en desarrollo,
    `/etc/wakemeup/config.toml` en despliegue (ver config.toml.example)."""
    project_config = Path(__file__).resolve().parent.parent.parent / "config" / "config.toml"
    if project_config.exists():
        return project_config
    return Path("/etc/wakemeup/config.toml")


class ScanSettings(BaseModel):
    """Sección `[scan]` del config.toml"""

    range: str = "192.168.1.0/24"
    interval_seconds: int = Field(default=600, ge=1)
    ttl_seconds: int = Field(default=60, ge=15, le=300)


class DbSettings(BaseModel):
    """Sección `[db]` del config.toml (story 1.4: ruta del SQLite, AD-7)."""

    path: str = "wakemeup.db"


class AuthSettings(BaseModel):
    """Sección `[auth]` del config.toml (backoff de autenticación, FR-10/AD-6)."""

    max_failures: int = Field(default=5, ge=1)
    window_seconds: int = Field(default=300, ge=1)
    block_seconds: int = Field(default=900, ge=1)


class SshSettings(BaseModel):
    """Sección `[ssh]` del config.toml (alta, story 2.1; AD-5/AD-9).

    Ruta del par de claves Ed25519 del BE (la privada con permisos 600, la usa
    solo el servicio como usuario no-root). `remote_home` es la ruta ABSOLUTA
    del home remoto usado para instalar `authorized_keys` (p. ej. `/home/maria`);
    vacía → se resuelve con `sftp.realpath(".")` del usuario conectado; `~`
    explícito es error (SFTP no expande el tilde). La password del alta vive
    solo en memoria del proceso (FR-6) — jamás en esta configuración.
    """

    key_path: str = "id_ed25519"
    remote_home: str = ""


class ShutdownSettings(BaseModel):
    """Sección `[shutdown]` del config.toml (apagado, story 2.3; AD-4).

    Comando único y sin shell libre ejecutado vía asyncssh como `remote_user`.
    Requiere sudoers NOPASSWD en la máquina remota restringido a este comando.
    """

    command: str = "sudo -n systemctl poweroff"


class ApiSettings(BaseModel):
    """Sección `[api]` del config.toml (bind, FR-9/AD-6; guard MCP epic 3).

    `bind_hosts`/`port`/`prefix` ya aparecían en el ejemplo; aquí se parsean
    para el guard solo-tailnet del MCP (AD-12). El bind real de uvicorn queda
    para la story 4.1.
    """

    bind_hosts: list[str] = Field(default_factory=lambda: ["127.0.0.1"])
    port: int = Field(default=8080, ge=1, le=65535)
    prefix: str = "/api/v1"


class Settings(BaseSettings):
    """Configuración completa; `[scan]` desde 1.2, `[db]`/`[auth]` desde 1.4,
    `[ssh]`/`[shutdown]` desde el epic 2."""

    model_config = SettingsConfigDict(
        env_prefix="WAKEMEUP_",
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
    )

    scan: ScanSettings = Field(default_factory=ScanSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    ssh: SshSettings = Field(default_factory=SshSettings)
    shutdown: ShutdownSettings = Field(default_factory=ShutdownSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        toml = TomlConfigSettingsSource(settings_cls, toml_file=default_config_path())
        return (init_settings, env_settings, toml)
