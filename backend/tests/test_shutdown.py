"""Tests del apagado por SSH (AC 2.3, spec epic 2): verificación estricta del
fingerprint (AD-2), no_fiable persistido en mismatch sin ejecutar nada, error
"sudo" claro (502), máquina no gestionada → 409 y registro de actividad
(FR-7/AD-9)."""
from __future__ import annotations

import pytest

from wakemeup.config import ShutdownSettings
from wakemeup.core.models import ActivityEntry, Machine
from wakemeup.services.activity import ActivityService
from wakemeup.services.shutdown import ShutdownError, ShutdownService


class FakeDb:
    """Doble de DB con máquinas y activity_log en memoria."""

    def __init__(self, machines: list[Machine]) -> None:
        self._machines = {m.id: m for m in machines}
        self.activities: list[ActivityEntry] = []
        self.no_fiable_calls: list[str] = []

    async def get_by_id(self, machine_id: int) -> Machine | None:
        return self._machines.get(machine_id)

    async def set_no_fiable(self, machine_id: int) -> None:
        self.no_fiable_calls.append(str(machine_id))

    async def record_activity(self, channel, token, machine_id, machine_ip, operation, result) -> None:
        self.activities.append(
            ActivityEntry(
                id=len(self.activities) + 1, timestamp="2026-09-14T00:00:00+00:00",
                channel=channel, token=token, machine_id=machine_id,
                machine_ip=machine_ip, operation=operation, result=result,
            )
        )

    async def begin(self) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class FakeSsh:
    """Doble del adaptador ssh con fingerprint configurable."""

    def __init__(self, fingerprint: str = "SHA256:abcd") -> None:
        self._fp = fingerprint
        self.connected: list[tuple[str, str]] = []
        self.commands: list[str] = []
        self.connect_error: Exception | None = None

    def set_current_fingerprint(self, value: str) -> None:
        self._fp = value

    async def connect_key(self, host: str, user: str):
        self.connected.append((host, user))
        if self.connect_error is not None:
            raise self.connect_error
        return object()

    async def read_host_fingerprint(self, connection) -> str:
        return self._fp

    async def run_command(self, connection, command: str) -> str:
        self.commands.append(command)
        return ""

    async def close(self, connection) -> None:
        pass


def _managed(id_: int = 1) -> Machine:
    return Machine(
        id=id_,
        ip=f"192.168.1.{10 + id_}",
        mac="aa:bb:cc:dd:ee:01",
        hostname=f"pc{id_}",
        fingerprint="SHA256:abcd",
        remote_user="maria",
    )


def _svc(db: FakeDb | None = None, ssh: FakeSsh | None = None):
    db = db or FakeDb([_managed()])
    ssh = ssh or FakeSsh()
    return (
        ShutdownService(
            db=db, ssh=ssh, activity=ActivityService(db), shutdown=ShutdownSettings()
        ),
        db,
        ssh,
    )


@pytest.mark.asyncio
async def test_shutdown_ok_runs_command_and_logs_api():
    """Fingerprint coincide → comando ejecutado; log canal api, resultado ok."""
    svc, db, ssh = _svc()
    result = await svc.shutdown(1)
    assert result == {"ok": True}
    assert ssh.commands == ["sudo -n systemctl poweroff"]
    a = db.activities[-1]
    assert a.operation == "shutdown"
    assert a.result == "ok"
    assert a.channel == "api"
    assert db.no_fiable_calls == []


@pytest.mark.asyncio
async def test_shutdown_fingerprint_mismatch_not_executed_and_no_fiable():
    """Mismatch → NO ejecuta, marca no_fiable y registra el evento (matriz)."""
    svc, db, ssh = _svc()
    ssh.set_current_fingerprint("SHA256:otro")
    with pytest.raises(ShutdownError) as exc_info:
        await svc.shutdown(1)
    assert exc_info.value.status_code == 409
    assert "no_fiable" in exc_info.value.message
    assert ssh.commands == []  # jamás se ejecuta el comando
    assert db.no_fiable_calls == ["1"]
    assert db.activities[-1].result == "fingerprint_mismatch"


@pytest.mark.asyncio
async def test_shutdown_sudo_error_502():
    """Sin sudoers NOPASSWD → error claro 'sudo' (502/503 envelope, matriz)."""
    from wakemeup.adapters.ssh import SshError

    class NoSudoSsh(FakeSsh):
        async def run_command(self, connection, command: str) -> str:
            raise SshError("sudo: el usuario remoto no tiene privilegios sudo (NOPASSWD)")

    svc, db, ssh = _svc(ssh=NoSudoSsh())
    with pytest.raises(ShutdownError) as exc_info:
        await svc.shutdown(1)
    assert exc_info.value.status_code == 502
    assert "sudo" in exc_info.value.message.lower()
    assert db.activities[-1].result == "sudo"
    assert db.no_fiable_calls == []


@pytest.mark.asyncio
async def test_shutdown_unmanaged_409():
    """Máquina no gestionada (depende del estado persistido, no del TTL) → 409."""
    db = FakeDb([Machine(id=2, ip="192.168.1.12", mac="aa:bb:cc:dd:ee:02")])
    svc, db2, ssh = _svc(db=db)
    with pytest.raises(ShutdownError) as exc_info:
        await svc.shutdown(2)
    assert exc_info.value.status_code == 409
    assert "gestionada" in exc_info.value.message
    assert ssh.connected == []
    assert db.activities[-1].result == "conflict"


@pytest.mark.asyncio
async def test_shutdown_unknown_machine_404():
    svc, db, ssh = _svc()
    with pytest.raises(ShutdownError) as exc_info:
        await svc.shutdown(999)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_shutdown_connection_failure_502():
    """Host caído / clave no aceptada → 502 con motivo claro."""
    from wakemeup.adapters.ssh import SshError

    class DownSsh(FakeSsh):
        async def connect_key(self, host: str, user: str):
            raise SshError("no se pudo conectar a 192.168.1.11: timeout")

    svc, db, ssh = _svc(ssh=DownSsh())
    with pytest.raises(ShutdownError) as exc_info:
        await svc.shutdown(1)
    assert exc_info.value.status_code == 502
    assert db.no_fiable_calls == []
    assert db.activities[-1].result == "error"


@pytest.mark.asyncio
async def test_shutdown_uses_remote_user_from_enrollment():
    """El shutdown usa exactamente `remote_user` del alta (AD-2)."""
    svc, db, ssh = _svc()
    await svc.shutdown(1)
    assert ssh.connected == [("192.168.1.11", "maria")]
