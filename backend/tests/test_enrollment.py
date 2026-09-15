"""Tests del alta de máquinas (AC 2.1, spec epics 2): asyncssh en proceso con
password de un solo uso (FR-6), fingerprint fijado, authorized_keys idempotente
y matriz de errores (401/404/409) con registro de actividad.

Los dobles de ssh imitan el contrato del adaptador; la password jamás acaba en
log ni en la BD.
"""
from __future__ import annotations

import re

import pytest

from wakemeup.core.models import ActivityEntry, Machine
from wakemeup.services.activity import ActivityService
from wakemeup.services.enrollment import EnrollmentError, EnrollmentService


class FakeDb:
    """Doble de DB: máquinas e activity_log en memoria."""

    def __init__(self, machines: list[Machine] | None = None) -> None:
        self._machines = {m.id: m for m in (machines or [])}
        self.activities: list[ActivityEntry] = []
        self.keys: list[tuple] = []
        self.managed_calls: list[str] = []

    async def get_by_id(self, machine_id: int) -> Machine | None:
        return self._machines.get(machine_id)

    async def set_managed(self, machine_id: int, fingerprint: str, remote_user: str) -> None:
        m = self._machines[machine_id]
        self._machines[machine_id] = Machine(
            id=m.id, ip=m.ip, mac=m.mac, hostname=m.hostname, state=m.state,
            status_checked_at=m.status_checked_at, fingerprint=fingerprint,
            remote_user=remote_user,
        )
        self.managed_calls.append(fingerprint)

    async def record_key(self, machine_id: int, algorithm: str, fingerprint_sha256: str) -> None:
        self.keys.append((machine_id, algorithm, fingerprint_sha256))

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


class FakeNet:
    """Doble de net (el alta no usa red de descubrimiento)."""

    async def wol_interface(self) -> str | None:
        return "eth0"


class FakeSsh:
    """Doble del adaptador ssh con script por test."""

    def __init__(self) -> None:
        self.connected: list[tuple[str, str]] = []
        self.installed: list[str] = []
        self.password_seen: list[str] = []
        self.fingerprint = "SHA256:abcd"
        self.install_ok = True
        self.connect_error: Exception | None = None

    def set_fingerprint(self, value: str) -> None:
        self.fingerprint = value

    def set_connect_error(self, exc: Exception) -> None:
        self.connect_error = exc

    def set_install_error(self, exc: Exception) -> None:
        self.install_error = exc

    async def connect(self, host: str, user: str, password: str):
        self.connected.append((host, user))
        self.password_seen.append(password)
        if self.connect_error is not None:
            raise self.connect_error
        return object()

    async def read_host_fingerprint(self, connection) -> str:
        return self.fingerprint

    async def install_authorized_key(self, connection) -> None:
        self.installed.append("authorized_keys")
        if getattr(self, "install_error", None) is not None:
            raise self.install_error

    async def close(self, connection) -> None:
        pass


def _svc(db: FakeDb | None = None, ssh: FakeSsh | None = None) -> tuple[EnrollmentService, FakeDb, FakeSsh]:
    db = db or FakeDb([Machine(id=1, ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10", hostname="pc")])
    ssh = ssh or FakeSsh()
    activity = ActivityService(db)
    return EnrollmentService(db=db, net=FakeNet(), ssh=ssh, activity=activity), db, ssh


@pytest.mark.asyncio
async def test_enroll_ok_fixes_fingerprint_and_installs_key():
    """Alta OK: fingerprint fijado, authorized_keys instalado, managed=true."""
    svc, db, ssh = _svc()
    result = await svc.enroll(1, "maria", "s3cr3t")
    assert result == {"id": 1, "managed": True}
    machine = db._machines[1]
    assert machine.fingerprint == "SHA256:abcd"
    assert machine.remote_user == "maria"
    assert ssh.installed == ["authorized_keys"]
    assert db.keys == [(1, "ed25519", "SHA256:abcd")]
    assert db.activities[-1].result == "ok"
    assert db.activities[-1].operation == "enroll"
    assert db.activities[-1].channel == "api"


@pytest.mark.asyncio
async def test_enroll_password_failed_401_and_not_managed():
    """Password fallida → 401; la máquina sigue no gestionada; evento registrado."""
    from wakemeup.adapters.ssh import AuthFailedError

    svc, db, ssh = _svc()
    ssh.set_connect_error(AuthFailedError("credenciales incorrectas"))
    with pytest.raises(EnrollmentError) as exc_info:
        await svc.enroll(1, "maria", "mala")
    exc = exc_info.value
    assert exc.status_code == 401
    assert "credenciales" in exc.message.lower()
    assert db._machines[1].fingerprint is None
    assert db.managed_calls == []
    assert db.activities[-1].result == "auth_failed"


@pytest.mark.asyncio
async def test_enroll_password_never_reaches_db_or_logs(caplog):
    """FR-6: la password no se persiste (activity_log) ni se loguea en el fallo."""
    import logging
    from wakemeup.adapters.ssh import AuthFailedError

    svc, db, ssh = _svc()
    ssh.set_connect_error(AuthFailedError("credenciales incorrectas"))
    with caplog.at_level(logging.WARNING, logger="wakemeup"):
        with pytest.raises(EnrollmentError):
            await svc.enroll(1, "maria", "s3cr3t-que-no-debe-ver-nadie")
    joined = " ".join(rec.getMessage() for rec in caplog.records)
    assert "s3cr3t-que-no-debe-ver-nadie" not in joined
    assert "s3cr3t-que-no-debe-ver-nadie" not in str(db.activities)


@pytest.mark.asyncio
async def test_enroll_already_managed_409_no_duplicate_key():
    """Máquina ya gestionada → 409 sin volver a instalar la clave."""
    db = FakeDb([
        Machine(id=1, ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10",
                fingerprint="SHA256:viejo", remote_user="ana"),
    ])
    svc, db2, ssh = _svc(db=db)
    with pytest.raises(EnrollmentError) as exc_info:
        await svc.enroll(1, "maria", "x")
    assert exc_info.value.status_code == 409
    assert "gestionada" in exc_info.value.message
    assert ssh.installed == []
    assert db.activities[-1].result == "conflict"


@pytest.mark.asyncio
async def test_enroll_unknown_machine_404():
    """Máquina inexistente → 404 not_found."""
    svc, db, ssh = _svc()
    with pytest.raises(EnrollmentError) as exc_info:
        await svc.enroll(999, "maria", "x")
    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "not_found"
    assert ssh.connected == []


@pytest.mark.asyncio
async def test_enroll_ssh_failure_502():
    """Fallo SSH genérico (host caído) → 502 con motivo claro."""
    from wakemeup.adapters.ssh import SshError

    svc, db, ssh = _svc()
    ssh.set_connect_error(SshError("no se pudo conectar a 192.168.1.10: timeout"))
    with pytest.raises(EnrollmentError) as exc_info:
        await svc.enroll(1, "maria", "x")
    assert exc_info.value.status_code == 502
    assert "conectar" in exc_info.value.message
    assert db._machines[1].fingerprint is None


@pytest.mark.asyncio
async def test_enroll_password_discarded_after_failure():
    """FR-6: aunque el alta falle, la conexión se cierra y no queda rastro."""
    from wakemeup.adapters.ssh import SshError

    svc, db, ssh = _svc()
    ssh.set_install_error(SshError("no se pudo crear ~/.ssh"))

    class TrackingSsh(FakeSsh):
        def __init__(self) -> None:
            super().__init__()
            self.closed: list[object] = []

        async def close(self, connection) -> None:
            self.closed.append(connection)

        async def install_authorized_key(self, connection) -> None:
            raise SshError("no se pudo crear ~/.ssh")

    ssh = TrackingSsh()
    svc = EnrollmentService(db=db, net=FakeNet(), ssh=ssh, activity=ActivityService(db))
    with pytest.raises(EnrollmentError):
        await svc.enroll(1, "maria", "s3cr3t")
    assert len(ssh.closed) == 1  # la conexión se cierra en el finally
    assert db._machines[1].fingerprint is None


@pytest.mark.asyncio
async def test_enroll_emits_event_with_channel_as_origin():
    """3.1/3.3: el alta emite `enroll_done` con origen = canal."""
    from wakemeup.services.events import EventBus

    bus = EventBus()
    db = FakeDb([Machine(id=1, ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10", hostname="pc")])
    svc = EnrollmentService(
        db=db, net=FakeNet(), ssh=FakeSsh(), activity=ActivityService(db), events=bus
    )
    async with bus.subscribe() as queue:
        await svc.enroll(1, "maria", "s3cr3t", channel="api")
        event = queue.get_nowait()
    assert event.type == "enroll_done"
    assert event.origin == "api"
    assert event.machine == "pc"


@pytest.mark.asyncio
async def test_activity_shape_has_no_password():
    """2.5: la entrada de actividad tiene el shape FR-11 sin passwords."""
    entry = db_activity = None
    svc, db, ssh = _svc()
    await svc.enroll(1, "maria", "s3cr3t")
    a = db.activities[-1]
    entry = " ".join(
        [a.timestamp, a.channel, a.token, str(a.machine_id), a.operation, a.result]
    )
    assert "s3cr3t" not in entry
    assert a.machine_ip == "192.168.1.10"
    assert a.result == "ok"
