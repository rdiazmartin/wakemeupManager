"""Configuración del BE (convención del spine): `config.toml` + override env.

Sección `[scan]` implementada en esta story; el resto de secciones del
`config.toml.example` entra con sus stories de consumo. Sin fichero, se usan
los defaults de código (escaneo cada 600 s, TTL 60 s, rango 192.168.1.0/24).
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


class Settings(BaseSettings):
    """Configuración completa; la sección `[scan]` se implementa en esta story."""

    model_config = SettingsConfigDict(
        env_prefix="WAKEMEUP_",
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
    )

    scan: ScanSettings = Field(default_factory=ScanSettings)

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
        toml = TomlConfigSettingsSource(settings_cls, toml_file=default_config_path())
        return (init_settings, env_settings, toml)
