"""Tests del instalador idempotente (story 4.1).

Con un `Runner` falso (no ejecuta nada real) y `tmp_path`: primera instalación
(detección AUTO de tailnet, usuario, claves 600/700, config, unidad, `uv sync`,
`enable --now`, healthcheck 200), re-ejecución idempotente (no regenera claves,
no pisa config, unidad idéntica, servicio intacto), ausencia de tailscale y
healthcheck fallido. Nada toca el host real.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from wakemeup.install import Installer, InstallPaths, render_unit


class FakeRunner:
    """Doble del `Runner`: registra comandos y simula efectos mínimos.

    `ssh-keygen` crea ficheros dummy (para poder comprobar permisos) y
    `chown`/`systemctl`/`useradd` solo se registran.
    """

    def __init__(self, *, user_exists: bool = False, tailscale: dict | None = None) -> None:
        self.calls: list[list[str]] = []
        self._user_exists = user_exists
        self._tailscale = tailscale

    def run(self, argv, *, check=True, cwd=None) -> subprocess.CompletedProcess:
        argv = list(argv)
        self.calls.append(argv)
        name = Path(argv[0]).name
        if name == "id":
            return subprocess.CompletedProcess(argv, 0 if self._user_exists else 1, "", "")
        if name == "useradd":
            self._user_exists = True  # el usuario queda creado en el "host" falso
            return subprocess.CompletedProcess(argv, 0, "", "")
        if name == "tailscale":
            return self._tailscale_call(argv)
        if name == "ssh-keygen":
            key = Path(argv[argv.index("-f") + 1])
            key.write_text("PRIVATE")
            os.chmod(key, 0o600)
            Path(str(key) + ".pub").write_text("ssh-ed25519 AAAA dummy")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def _tailscale_call(self, argv) -> subprocess.CompletedProcess:
        if self._tailscale is None:
            return subprocess.CompletedProcess(argv, 1, "", "tailscale: not found")
        if argv[1] == "ip":
            return subprocess.CompletedProcess(argv, 0, self._tailscale.get("ip", ""), "")
        if argv[1] == "status":
            payload = json.dumps(self._tailscale.get("status", {}))
            return subprocess.CompletedProcess(argv, 0, payload, "")
        return subprocess.CompletedProcess(argv, 0, "", "")


def _paths(tmp_path: Path) -> InstallPaths:
    return InstallPaths(
        app_dir=tmp_path / "opt" / "backend",
        user="wakemeup-test",
        data_dir=tmp_path / "var" / "wakemeup",
        config_path=tmp_path / "etc" / "config.toml",
        unit_path=tmp_path / "etc" / "wakemeup.service",
        port=18080,
    )


def _tailscale_present() -> dict:
    return {
        "ip": "100.77.163.61\n",
        "status": {"Self": {"DNSName": "testing.tailnet.ts.net."}},
    }


def _installer(tmp_path, runner, *, status=200):
    return Installer(runner, _paths(tmp_path), http_get=lambda url, timeout: status)


def test_first_install_creates_everything(tmp_path: Path) -> None:
    runner = FakeRunner(tailscale=_tailscale_present())
    installer = _installer(tmp_path, runner)
    assert installer.run() == 0
    paths = _paths(tmp_path)

    assert paths.key_path.exists()
    assert stat.S_IMODE(paths.key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(paths.key_dir.stat().st_mode) == 0o700

    assert paths.config_path.exists()
    assert stat.S_IMODE(paths.config_path.stat().st_mode) == 0o600
    config = paths.config_path.read_text()
    assert '"100.77.163.61"' in config
    assert '"127.0.0.1"' in config
    assert 'extra_allowed_hosts = ["testing.tailnet.ts.net"]' in config
    assert f'path = "{paths.db_path}"' in config
    assert f'key_path = "{paths.key_path}"' in config

    assert paths.unit_path.exists()
    unit = paths.unit_path.read_text()
    assert f"ExecStart={paths.venv_python} -m wakemeup.server" in unit
    assert "After=network-online.target tailscaled.service" in unit
    assert "Restart=always" in unit

    flat = [" ".join(c) for c in runner.calls]
    assert any("useradd" in c for c in flat)
    assert any("ssh-keygen" in c for c in flat)
    assert any("uv sync --frozen" in c for c in flat)
    assert any("systemctl enable --now wakemeup.service" in c for c in flat)


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    runner = FakeRunner(tailscale=_tailscale_present())
    installer = _installer(tmp_path, runner)
    assert installer.run() == 0
    paths = _paths(tmp_path)

    # El operador edita la config a mano y se altera la "clave" privada.
    custom = "# config editada a mano\n"
    paths.config_path.write_text(custom)
    paths.key_path.write_text("ORIGINAL-KEY")
    os.chmod(paths.config_path, 0o600)

    runner.calls.clear()
    assert installer.run() == 0

    assert paths.config_path.read_text() == custom  # config conservada
    assert paths.key_path.read_text() == "ORIGINAL-KEY"  # clave no regenerada
    flat = [" ".join(c) for c in runner.calls]
    assert not any("ssh-keygen" in c for c in flat)
    assert not any("useradd" in c for c in flat)
    # La unidad no cambia; el enable se repite (idempotente) y el servicio sigue.
    assert any("systemctl enable --now wakemeup.service" in c for c in flat)


def test_unit_rewritten_only_when_content_changes(tmp_path: Path) -> None:
    runner = FakeRunner(tailscale=_tailscale_present())
    installer = _installer(tmp_path, runner)
    installer.run()
    paths = _paths(tmp_path)
    mtime_before = paths.unit_path.stat().st_mtime_ns

    # Contenido distinto → se reescribe.
    paths.unit_path.write_text("obsoleto")
    assert installer.write_unit() is True
    assert paths.unit_path.read_text() == render_unit(paths)
    assert paths.unit_path.stat().st_mtime_ns != mtime_before

    # Contenido idéntico → no se reescribe.
    assert installer.write_unit() is False


def test_without_tailscale_loopback_only(tmp_path: Path) -> None:
    runner = FakeRunner(tailscale=None)  # `tailscale ip -4` rc≠0
    installer = _installer(tmp_path, runner)
    assert installer.run() == 0
    config = _paths(tmp_path).config_path.read_text()
    assert 'bind_hosts = ["127.0.0.1"]' in config
    assert 'extra_allowed_hosts = []' in config


def test_tailscale_without_ip_yet(tmp_path: Path) -> None:
    """`tailscale` presente pero sin IP aún → loopback, pero DNSName sí se guarda."""
    runner = FakeRunner(tailscale={"ip": "", "status": {"Self": {"DNSName": "t.ts.net."}}})
    installer = _installer(tmp_path, runner)
    assert installer.run() == 0
    config = _paths(tmp_path).config_path.read_text()
    assert 'bind_hosts = ["127.0.0.1"]' in config
    assert 'extra_allowed_hosts = ["t.ts.net"]' in config


def test_healthcheck_failure_returns_nonzero(tmp_path: Path) -> None:
    """Si el healthcheck no da 200, `run()` devuelve rc≠0 (fallo explícito)."""
    runner = FakeRunner(tailscale=_tailscale_present())
    installer = Installer(runner, _paths(tmp_path), http_get=lambda url, timeout: 503)
    installer.healthcheck = lambda **_: False  # type: ignore[method-assign]
    assert installer.run() == 1


def test_healthcheck_success_and_retry() -> None:
    attempts = {"n": 0}

    def flaky(url: str, timeout: float) -> int:
        attempts["n"] += 1
        return 200 if attempts["n"] >= 2 else 599

    installer = Installer(
        FakeRunner(), InstallPaths(), http_get=flaky
    )
    assert installer.healthcheck(timeout=5.0, interval=0.0) is True
    assert attempts["n"] >= 2


def test_healthcheck_failure_after_timeout() -> None:
    installer = Installer(
        FakeRunner(), InstallPaths(), http_get=lambda url, timeout: 500
    )
    assert installer.healthcheck(timeout=0.05, interval=0.0) is False
