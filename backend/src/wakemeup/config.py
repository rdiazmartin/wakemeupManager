"""Configuración del BE (convención del spine): `config.toml` + override env.

Sección `[scan]` (1.2), `[db]` y `[auth]` (1.4). Sin fichero, se usan los
defaults de código (escaneo cada 600 s, TTL 60 s, rango 192.168.1.0/24,
DB `wakemeup.db` en CWD, backoff 5 fallos/5 min → 429/15 min).
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


class Settings(BaseSettings):
    """Configuración completa; `[scan]` desde la story 1.2, `[db]`/`[auth]` desde 1.4."""

    model_config = SettingsConfigDict(
        env_prefix="WAKEMEUP_",
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
    )

    scan: ScanSettings = Field(default_factory=ScanSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        toml = TomlConfigSettingsSource(settings_cls, toml_file=default_config_path())
        return (init_settings, env_settings, toml)
