"""Tests de configuración: defaults sin fichero, TOML real y override por env (spec 1.2)."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from wakemeup.config import ScanSettings, Settings


def test_defaults_without_config_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin config.toml ni env, se usan los defaults `[scan]` (FR-3/AD-3)."""
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: "/nonexistent/config.toml")
    settings = Settings()
    assert settings.scan.range == "192.168.1.0/24"
    assert settings.scan.interval_seconds == 600
    assert settings.scan.ttl_seconds == 60


def test_toml_file_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """La fuente real del spine (config.toml) puebla `[scan]` (verificación-gap)."""
    toml = tmp_path / "config.toml"
    toml.write_text(
        "[scan]\n"
        'range = "10.10.10.0/30"\n'
        "interval_seconds = 300\n"
        "ttl_seconds = 120\n"
    )
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: str(toml))
    settings = Settings()
    assert settings.scan.range == "10.10.10.0/30"
    assert settings.scan.interval_seconds == 300
    assert settings.scan.ttl_seconds == 120


def test_env_wins_over_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Override por env (WAKEMEUP_SCAN__*) vence sobre el config.toml (convención)."""
    toml = tmp_path / "config.toml"
    toml.write_text('[scan]\nrange = "10.10.10.0/30"\n')
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: str(toml))
    monkeypatch.setenv("WAKEMEUP_SCAN__RANGE", "192.168.10.0/24")
    settings = Settings()
    assert settings.scan.range == "192.168.10.0/24"


def test_scan_settings_bounds() -> None:
    """Bounds del AD-2/FR-2: TTL entre 15 y 300 s."""
    with pytest.raises(ValidationError):
        ScanSettings(ttl_seconds=5)
