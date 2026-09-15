"""Tests del wake WOL (AC 2.2, spec epic 2): validación de MAC (422/`ValueError`),
un único magic packet al broadcast, sin retry, éxito degradado sin Ethernet y
errores 404/409 con registro de actividad (FR-4)."""
from __future__ import annotations

import pytest

from wakemeup.core.models import ActivityEntry, Machine
from wakemeup.services.activity import ActivityService
from wakemeup.services.wake import WakeError, WakeService


class FakeDb:
    """Doble de DB con máquinas y activity_log en memoria."""

    def __init__(self, machines: list[Machine]) -> None:
        self._machines = {m.id: m for m in machines}
        self.activities: list[ActivityEntry] = []

    async def get_by_id(self, machine_id: int) -> Machine | None:
        return self._machines.get(machine_id)

    async def begin(self) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    async def set_last_change(self, machine_id: int, origin: str) -> None:
        self.last_changes = getattr(self, "last_changes", [])
        self.last_changes.append((machine_id, origin))

    async def record_activity(self, channel, token, machine_id, machine_ip, operation, result) -> None:
        self.activities.append(
            ActivityEntry(
                id=len(self.activities) + 1, timestamp="2026-09-14T00:00:00+00:00",
                channel=channel, token=token, machine_id=machine_id,
                machine_ip=machine_ip, operation=operation, result=result,
            )
        )


class FakeNet:
    """Doble de net: registra el envío y puede simular 'sin Ethernet'."""

    def __init__(self, ethernet: bool = True, sends: list[str] | None = None) -> None:
        self.sends = sends if sends is not None else []
        self.ethernet = ethernet

    async def wol_interface(self) -> str | None:
        return "eth0" if self.ethernet else None

    async def send_wol(self, mac: str, interface: str | None = None) -> str:
        self.sends.append(mac)
        if not self.ethernet:
            from wakemeup.adapters.net import NoWolInterfaceError

            raise NoWolInterfaceError("sin interfaz Ethernet con carrier")
        return "eth0"

    def normalize_mac(self, mac: str) -> bytes:
        from wakemeup.adapters.net import Net

        return Net.normalize_mac(mac)


def _managed(id_: int, mac: str | None = "aa:bb:cc:dd:ee:01") -> Machine:
    return Machine(
        id=id_, ip=f"192.168.1.{10 + id_}", mac=mac, hostname=f"pc{id_}",
        fingerprint="SHA256:abcd", remote_user="maria",
    )


def _svc(net: FakeNet | None = None, machines: list[Machine] | None = None):
    net = net or FakeNet()
    db = FakeDb(machines or [])
    return WakeService(db=db, net=net, activity=ActivityService(db)), db, net


@pytest.mark.asyncio
async def test_wake_sends_single_packet_and_returns_ok():
    """Matriz: wake OK → un único envío y `{"ok": true}`."""
    svc, db, net = _svc(machines=[_managed(1)])
    result = await svc.wake(1)
    assert result == {"ok": True}
    assert net.sends == ["aa:bb:cc:dd:ee:01"]
    assert db.activities[-1].result == "ok"


@pytest.mark.asyncio
async def test_wake_magic_packet_structure():
    """6×0xFF + MAC×16 se construye en `normalize_mac` (12 o 17 con :/-)."""
    from wakemeup.adapters.net import Net

    normalized = Net.normalize_mac("AA-BB-CC-DD-EE-01")
    assert normalized == b"\xaa\xbb\xcc\xdd\xee\x01"
    assert Net.normalize_mac("aabbccddee01") == normalized


@pytest.mark.asyncio
async def test_wake_invalid_mac_422():
    """MAC malformada → 422 validation_error (matriz)."""
    db = FakeDb([Machine(id=1, ip="192.168.1.11", mac="zz:bb:cc:dd:ee:01", fingerprint="SHA256:x", remote_user="u")])
    svc = WakeService(db=db, net=FakeNet(), activity=ActivityService(db))
    with pytest.raises(WakeError) as exc_info:
        await svc.wake(1)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "validation_error"
    assert db.activities[-1].result == "validation_error"


@pytest.mark.asyncio
async def test_wake_multicast_and_zero_mac_422():
    """MAC multicast o cero → 422 (matriz)."""
    for mac in ("01:00:00:00:00:01", "00:00:00:00:00:00"):
        db = FakeDb([Machine(id=1, ip="192.168.1.11", mac=mac, fingerprint="SHA256:x", remote_user="u")])
        svc = WakeService(db=db, net=FakeNet(), activity=ActivityService(db))
        with pytest.raises(WakeError) as exc_info:
            await svc.wake(1)
        assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_wake_unknown_machine_404():
    svc, db, net = _svc(machines=[_managed(1)])
    with pytest.raises(WakeError) as exc_info:
        await svc.wake(999)
    assert exc_info.value.status_code == 404
    assert net.sends == []


@pytest.mark.asyncio
async def test_wake_unmanaged_machine_409():
    """Discovery-only (sin fingerprint) → 409 'no gestionada' (matriz)."""
    db = FakeDb([Machine(id=2, ip="192.168.1.12", mac="aa:bb:cc:dd:ee:02")])
    svc = WakeService(db=db, net=FakeNet(), activity=ActivityService(db))
    with pytest.raises(WakeError) as exc_info:
        await svc.wake(2)
    assert exc_info.value.status_code == 409
    assert "gestionada" in exc_info.value.message
    assert db.activities[-1].result == "conflict"


@pytest.mark.asyncio
async def test_wake_without_mac_409():
    """Sin MAC fijada no se puede enviar WOL → 409 (matriz)."""
    db = FakeDb([Machine(id=3, ip="192.168.1.13", mac=None, fingerprint="SHA256:x", remote_user="u")])
    svc = WakeService(db=db, net=FakeNet(), activity=ActivityService(db))
    with pytest.raises(WakeError) as exc_info:
        await svc.wake(3)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_wake_without_ethernet_is_success_with_warning():
    """Sin interfaz Ethernet → 200 éxito degradado (warning), sin romper (matriz)."""
    svc, db, net = _svc(net=FakeNet(ethernet=False), machines=[_managed(1)])
    result = await svc.wake(1)
    assert result == {"ok": True}
    assert db.activities[-1].result == "ok_no_ethernet"


@pytest.mark.asyncio
async def test_wake_no_retry_single_send():
    """FR-4: un solo envío por petición, sin reintento automático."""
    sends: list[str] = []

    class OneSendNet(FakeNet):
        async def send_wol(self, mac: str, interface: str | None = None) -> str:
            sends.append(mac)
            return "eth0"

    svc, db, net = _svc(net=OneSendNet(), machines=[_managed(1)])
    await svc.wake(1)
    assert sends == ["aa:bb:cc:dd:ee:01"]


@pytest.mark.asyncio
async def test_wake_emits_event_with_channel_as_origin():
    """3.1/3.3: el wake emite `wake_sent` con origen = canal (mcp para el agente)."""
    from wakemeup.services.events import EventBus

    bus = EventBus()
    db = FakeDb([_managed(1)])
    svc = WakeService(db=db, net=FakeNet(), activity=ActivityService(db), events=bus)
    async with bus.subscribe() as queue:
        await svc.wake(1, channel="mcp")
        event = queue.get_nowait()
    assert event.type == "wake_sent"
    assert event.origin == "mcp"
    assert event.machine == "pc1"
    assert db.last_changes == [(1, "mcp")]
