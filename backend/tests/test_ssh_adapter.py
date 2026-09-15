"""Tests del adaptador SSH (epic 2, regresión del alta real).

La verificación en dispositivo (tablet + testing) descubrió dos fallos en el
camino del alta que los dobles de los servicios no cubrían:
1. `_resolve_remote_home` no awaitaba `sftp.realpath` → 500.
2. `public_key.rstrip` sobre bytes de `export_public_key` → TypeError → 500.

Estos tests fijan el contrato con dobles de SFTP (sin servidor real, que no
es viable en CI) y cubren `install_authorized_key` de extremo a extremo.
"""
from __future__ import annotations

import types

import pytest

from wakemeup.adapters.ssh import Ssh, SshError, _public_key_line
from wakemeup.config import SshSettings

_PUB_BYTES = b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyWakemeupTest backend\n"


class _FakeSftp:
    """Doble de SFTP con `realpath` async, fichero virtual y setstat."""

    def __init__(self, home: str = "/home/roberto", existing: bytes | None = None) -> None:
        self.home = home
        self.files: dict[str, bytes] = {}
        self.modes: dict[str, int] = {}
        self.mkdirs: list[str] = []
        if existing is not None:
            self.files[f"{home}/.ssh/authorized_keys"] = existing

    async def realpath(self, path: str) -> str:
        assert path == "."
        return self.home

    async def stat(self, path: str) -> object:
        if path in self.files or path in self.mkdirs:
            return object()
        import asyncssh

        raise asyncssh.SFTPNoSuchFile(path)

    async def mkdir(self, path: str) -> None:
        self.mkdirs.append(path)

    async def setstat(self, path: str, attrs) -> None:
        self.modes[path] = attrs.permissions

    def open(self, path: str, mode: str):
        sftp = self

        class _Fh:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def read(self) -> bytes:
                return sftp.files.get(path, b"")

            async def write(self, data) -> None:
                sftp.files[path] = data.encode() if isinstance(data, str) else data

        return _Fh()


class _FakeConnection:
    def __init__(self, sftp: _FakeSftp) -> None:
        self._sftp = sftp

    def start_sftp_client(self):
        sftp = self._sftp

        class _Ctx:
            async def __aenter__(self):
                return sftp

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _ssh_with_key(tmp_path, remote_home: str = "") -> Ssh:
    """Ssh con clave real en tmp: `_load_client_keys` no requiere red."""
    import asyncssh

    key = asyncssh.generate_private_key("ssh-ed25519")
    path = tmp_path / "id_ed25519"
    path.write_bytes(key.export_private_key("openssh", passphrase=None))
    path.chmod(0o600)
    return Ssh(SshSettings(key_path=str(path), remote_home=remote_home))


def test_public_key_line_normalizes_bytes_and_str():
    """`export_public_key` puede devolver bytes o str: siempre línea str sin \\n."""
    assert _public_key_line(_PUB_BYTES) == _PUB_BYTES.decode().rstrip("\n")
    assert _public_key_line(_PUB_BYTES.decode()) == _PUB_BYTES.decode().rstrip("\n")


@pytest.mark.asyncio
async def test_resolve_remote_home_configured_absolute():
    """Ruta absoluta configurada → se usa tal cual (sin tocar SFTP)."""
    ssh = Ssh(SshSettings(remote_home="/home/maria"))
    assert await ssh._resolve_remote_home(_FakeSftp()) == "/home/maria"


@pytest.mark.asyncio
async def test_resolve_remote_home_empty_uses_async_realpath():
    """Regresión 1: `remote_home` vacío awaita `sftp.realpath` (era un 500)."""
    ssh = Ssh(SshSettings(remote_home=""))
    assert await ssh._resolve_remote_home(_FakeSftp("/home/roberto")) == "/home/roberto"


@pytest.mark.asyncio
async def test_resolve_remote_home_tilde_is_a_clear_error():
    """`~` explícito → error claro (SFTP no expande tilde)."""
    ssh = Ssh(SshSettings(remote_home="~"))
    with pytest.raises(SshError, match="tilde"):
        await ssh._resolve_remote_home(_FakeSftp())


@pytest.mark.asyncio
async def test_install_authorized_key_end_to_end_creates_dir_key_and_mode(tmp_path):
    """Regresión 2: alta real — instala la clave (bytes) con 0600 y sin TypeError."""
    ssh = _ssh_with_key(tmp_path)
    sftp = _FakeSftp()
    await ssh.install_authorized_key(_FakeConnection(sftp))

    path = "/home/roberto/.ssh/authorized_keys"
    assert "/home/roberto/.ssh" in sftp.mkdirs
    assert path in sftp.files
    assert sftp.files[path].decode().startswith("ssh-ed25519 ")
    assert sftp.modes[path] == 0o600
    assert sftp.modes["/home/roberto/.ssh"] == 0o700


@pytest.mark.asyncio
async def test_install_authorized_key_is_idempotent(tmp_path):
    """Doble alta: la clave ya presente NO se duplica (idempotencia)."""
    ssh = _ssh_with_key(tmp_path)
    _, own_line = None, None
    key = await ssh._load_client_keys()
    own_line = _public_key_line(key.export_public_key())

    existing = f"ssh-rsa AAAAotraClave vieja\n{own_line}\n".encode()
    sftp = _FakeSftp(existing=existing)
    await ssh.install_authorized_key(_FakeConnection(sftp))

    content = sftp.files["/home/roberto/.ssh/authorized_keys"].decode()
    assert content.count(own_line) == 1
