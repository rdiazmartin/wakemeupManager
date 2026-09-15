"""Tests de configuración: defaults sin fichero, TOML real y override por env (spec 1.2 + epic 2)."""
from pathlib import Path

import pytest
from pydantic import ValidationError

from wakemeup.config import AuthSettings, ScanSettings, Settings, ShutdownSettings, SshSettings


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


def test_db_section_defaults_and_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sección `[db]` (story 1.4): default CWD y override TOML/env.

    CIERRA el deferred-work de 1.2 (ruta SQLite CWD-relative): la ruta es
    configurable y el default conserva el comportamiento actual.
    """
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: "/nonexistent/config.toml")
    assert Settings().db.path == "wakemeup.db"

    toml = tmp_path / "config.toml"
    toml.write_text('[db]\npath = "/var/lib/wakemeup/wakemeup.db"\n')
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: str(toml))
    assert Settings().db.path == "/var/lib/wakemeup/wakemeup.db"

    monkeypatch.setenv("WAKEMEUP_DB__PATH", "/tmp/otra.db")
    assert Settings().db.path == "/tmp/otra.db"


def test_auth_section_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sección `[auth]`: defaults del backoff (FR-10/AD-6) y bounds."""
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: "/nonexistent/config.toml")
    settings = Settings()
    assert settings.auth.max_failures == 5
    assert settings.auth.window_seconds == 300
    assert settings.auth.block_seconds == 900
    with pytest.raises(ValidationError):
        AuthSettings(max_failures=0)


def test_ssh_and_shutdown_sections_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Epic 2: `[ssh]` (claves del BE) y `[shutdown]` (comando único, AC 2.3)."""
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: "/nonexistent/config.toml")
    settings = Settings()
    assert settings.ssh.key_path == "id_ed25519"
    assert settings.ssh.remote_home == ""  # vacío → el alta resuelve el home vía SFTP
    assert settings.shutdown.command == "sudo -n systemctl poweroff"


def test_api_section_defaults_and_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Epic 3: `[api]` (bind/port/prefix) se parsea para el guard solo-tailnet."""
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: "/nonexistent/config.toml")
    settings = Settings()
    assert settings.api.bind_hosts == ["127.0.0.1"]
    assert settings.api.port == 8080
    assert settings.api.prefix == "/api/v1"

    toml = tmp_path / "config.toml"
    toml.write_text(
        "[api]\n"
        'bind_hosts = ["100.100.100.1", "127.0.0.1"]\n'
        "port = 9000\n"
        'prefix = "/api/v1"\n'
    )
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: str(toml))
    settings = Settings()
    assert settings.api.bind_hosts == ["100.100.100.1", "127.0.0.1"]
    assert settings.api.port == 9000


def test_transport_security_allowlist_default_and_ipv6(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Epic 3: el allowlist del MCP incluye loopback y los `bind_hosts`.

    Un `bind_hosts` IPv6 debe ir con corchetes (`[fd7a::1]:*`); sin ellos el
    patrón es malformado y el `Host` header nunca casaría.
    """
    from wakemeup.api import _build_transport_security

    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: "/nonexistent/config.toml")
    security = _build_transport_security()
    assert security.enable_dns_rebinding_protection is True
    assert "127.0.0.1:*" in security.allowed_hosts
    assert "localhost:*" in security.allowed_hosts
    assert "[::1]:*" in security.allowed_hosts
    assert "127.0.0.1:*" in security.allowed_hosts  # bind_hosts default

    toml = tmp_path / "config.toml"
    toml.write_text(
        "[api]\n"
        'bind_hosts = ["fd7a:115c:a1e0::1", "100.100.100.1"]\n'
    )
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: str(toml))
    security = _build_transport_security()
    assert "[fd7a:115c:a1e0::1]:*" in security.allowed_hosts
    assert "100.100.100.1:*" in security.allowed_hosts
    assert "fd7a:115c:a1e0::1:*" not in security.allowed_hosts


def test_ssh_and_shutdown_toml_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    toml = tmp_path / "config.toml"
    toml.write_text(
        "[ssh]\n"
        'key_path = "/var/lib/wakemeup/keys/id_ed25519"\n'
        'remote_home = "/home/maria"\n'
        "[shutdown]\n"
        'command = "sudo -n shutdown -h now"\n'
    )
    monkeypatch.setattr("wakemeup.config.default_config_path", lambda: str(toml))
    settings = Settings()
    assert settings.ssh.key_path == "/var/lib/wakemeup/keys/id_ed25519"
    assert settings.ssh.remote_home == "/home/maria"
    assert settings.shutdown.command == "sudo -n shutdown -h now"

