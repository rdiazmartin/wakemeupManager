"""Tests del servicio de descubrimiento: upsert, singleflight e inventario.

Cubre la matriz del spec 1.2: dos escaneos concurrentes → solo uno efectivo
(AD-3), hosts vivos persistidos con IP/MAC/hostname y host apagado que ya
estaba inventariado que no se borra (upsert no destructivo).
"""
import asyncio

import pytest

from wakemeup.adapters.db import Db
from wakemeup.adapters.net import Net
from wakemeup.config import ScanSettings
from wakemeup.core.models import HostInfo
from wakemeup.services.discovery import DiscoveryService


class FakeNet(Net):
    """Net que devuelve hosts fijos y cuenta escaneos reales."""

    def __init__(self, hosts: list[HostInfo]) -> None:
        super().__init__(ping="fakeping")
        self.hosts = hosts
        self.scans: list[str] = []
        self.active_scans = 0
        self.max_active_scans = 0
        self.release: asyncio.Event | None = None

    async def scan_range(self, cidr: str) -> list[HostInfo]:
        self.scans.append(cidr)
        self.active_scans += 1
        self.max_active_scans = max(self.max_active_scans, self.active_scans)
        try:
            if self.release is not None:
                await asyncio.wait_for(self.release.wait(), timeout=2)
            return list(self.hosts)
        finally:
            self.active_scans -= 1

    async def read_arp_table(self) -> dict[str, str]:
        return {}


@pytest.fixture
async def db(tmp_path):
    db = Db(path=tmp_path / "test.db")
    await db.init_db()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_scan_upserts_hosts_in_inventory(db, tmp_path):
    net = FakeNet(
        [
            HostInfo(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10", hostname="pc-maria"),
            HostInfo(ip="192.168.1.11", mac=None, hostname=None),
        ]
    )
    svc = DiscoveryService(net=net, db=db, scan=ScanSettings())

    count = await svc.scan()
    assert count == 2

    machines = await db.list_machines()
    by_ip = {m.ip: m for m in machines}
    assert set(by_ip) == {"192.168.1.10", "192.168.1.11"}
    assert by_ip["192.168.1.10"].mac == "aa:bb:cc:dd:ee:10"
    assert by_ip["192.168.1.10"].hostname == "pc-maria"
    assert by_ip["192.168.1.11"].mac is None
    assert net.scans == ["192.168.1.0/24"]


@pytest.mark.asyncio
async def test_scan_upserts_is_idempotent(db):
    net = FakeNet([HostInfo(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10")])
    svc = DiscoveryService(net=net, db=db, scan=ScanSettings())

    await svc.scan()
    await svc.scan()

    machines = await db.list_machines()
    assert len(machines) == 1
    assert machines[0].ip == "192.168.1.10"


@pytest.mark.asyncio
async def test_upsert_preserves_existing_mac_and_hostname(db):
    """Upsert no destructivo (verificación-gap): un re-escaneo con mac/hostname
    ausentes conserva los valores ya almacenados (nunca se borran)."""
    net = FakeNet(
        [
            HostInfo(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10", hostname="pc-maria"),
        ]
    )
    svc = DiscoveryService(net=net, db=db, scan=ScanSettings())
    await svc.scan()

    net.hosts = [HostInfo(ip="192.168.1.10")]  # re-escaneo sin MAC ni hostname
    await svc.scan()

    machines = await db.list_machines()
    assert len(machines) == 1
    assert machines[0].mac == "aa:bb:cc:dd:ee:10"
    assert machines[0].hostname == "pc-maria"


@pytest.mark.asyncio
async def test_scan_mid_upsert_failure_rolls_back(db):
    """Transacción (revisión 1.2): un fallo a mitad del upsert revierte todo
    y el inventario no queda parcialmente actualizado."""
    class FailingDb(Db):
        async def upsert_machine(self, ip, mac=None, hostname=None):
            if ip == "192.168.1.11":
                raise RuntimeError("upsert caído")
            return await super().upsert_machine(ip, mac, hostname)

    db2 = FailingDb(path=db._path)
    await db2.init_db()
    net = FakeNet(
        [
            HostInfo(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10"),
            HostInfo(ip="192.168.1.11", mac="aa:bb:cc:dd:ee:11"),
        ]
    )
    svc = DiscoveryService(net=net, db=db2, scan=ScanSettings())
    with pytest.raises(RuntimeError):
        await svc.scan()
    machines = await db2.list_machines()
    assert [m.ip for m in machines] == []


@pytest.mark.asyncio
async def test_no_upsert_for_down_hosts(db):
    net = FakeNet([])
    svc = DiscoveryService(net=net, db=db, scan=ScanSettings())
    await svc.scan()
    assert await db.list_machines() == []


@pytest.mark.asyncio
async def test_singleflight_two_concurrent_scans_run_one(db):
    net = FakeNet([HostInfo(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10")])
    svc = DiscoveryService(net=net, db=db, scan=ScanSettings())

    results = await asyncio.gather(svc.scan(), svc.scan())
    assert results == [1, 1]
    assert len(net.scans) == 1
    assert net.max_active_scans == 1


@pytest.mark.asyncio
async def test_second_scan_waits_for_in_flight(db):
    net = FakeNet([HostInfo(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10")])
    net.release = asyncio.Event()
    svc = DiscoveryService(net=net, db=db, scan=ScanSettings())

    first = asyncio.create_task(svc.scan())
    await asyncio.sleep(0.05)
    assert svc.in_flight is True

    second = asyncio.create_task(svc.scan())
    await asyncio.sleep(0.05)
    assert len(net.scans) == 1  # el segundo no arranca otro escaneo

    net.release.set()
    assert await asyncio.gather(first, second) == [1, 1]
    assert len(net.scans) == 1


@pytest.mark.asyncio
async def test_current_task_reflects_live_scan_including_upsert(db):
    """current_task (deferred 1.2, verification-gap 1.4): durante el escaneo
    completo —incluida la fase de upsert— la propiedad devuelve la tarea viva,
    y tras terminar devuelve None."""
    class SlowDb(Db):
        def __init__(self, path) -> None:
            super().__init__(path)
            self.release_upsert = asyncio.Event()

        async def upsert_machine(self, ip, mac=None, hostname=None):
            await asyncio.wait_for(self.release_upsert.wait(), timeout=2)
            return await super().upsert_machine(ip, mac, hostname)

    slow_db = SlowDb(path=db._path)
    await slow_db.init_db()
    net = FakeNet([HostInfo(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10")])
    svc = DiscoveryService(net=net, db=slow_db, scan=ScanSettings())

    first = asyncio.create_task(svc.scan())
    await asyncio.sleep(0.05)
    # escaneo en marcha, bloqueado en el upsert → la TAREA queda viva aunque
    # in_flight ya es False (justo el hueco que cerraba el deferred 1.2).
    assert svc.in_flight is False
    assert svc.current_task is not None
    assert not svc.current_task.done()

    slow_db.release_upsert.set()
    assert await first == 1
    assert svc.current_task is None

    await slow_db.close()


@pytest.mark.asyncio
async def test_stop_cancels_periodic_loop(db):
    """stop() (verification-gap 1.4): cancela el loop periódico real."""
    net = FakeNet([HostInfo(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10")])
    svc = DiscoveryService(
        net=net, db=db, scan=ScanSettings(interval_seconds=1, ttl_seconds=60)
    )
    task = svc.periodic_task()
    await asyncio.sleep(0.2)
    assert len(net.scans) >= 1

    svc.stop()
    await asyncio.sleep(0)  # cede el ciclo al event loop para entregar el cancel
    assert task.cancelled()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_periodic_loop_calls_scan(db, tmp_path):
    net = FakeNet([HostInfo(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10")])
    svc = DiscoveryService(
        net=net, db=db, scan=ScanSettings(interval_seconds=1, ttl_seconds=60)
    )

    task = svc.periodic_task()
    try:
        # el loop escanea al arrancar (antes del primer sleep)
        await asyncio.sleep(0.2)
        assert len(net.scans) >= 1
        machines = await db.list_machines()
        assert any(m.ip == "192.168.1.10" for m in machines)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_periodic_loop_survives_failing_scan(db):
    """Recuperación (verificación-gap): un escaneo que falla no mata el loop;
    el siguiente tick reintenta."""
    class FlakyNet(FakeNet):
        def __init__(self) -> None:
            super().__init__([HostInfo(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10")])
            self.fail_next = True

        async def scan_range(self, cidr: str):
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("ping roto temporalmente")
            return await super().scan_range(cidr)

    net = FlakyNet()
    svc = DiscoveryService(
        net=net, db=db, scan=ScanSettings(interval_seconds=1, ttl_seconds=60)
    )

    async def wait_until(predicate, timeout_s: float = 3.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.05)
        raise AssertionError("condición no alcanzada a tiempo")

    task = svc.periodic_task()
    try:
        # primer tick: falla; el loop sigue vivo (fail_next consumido)
        await wait_until(lambda: net.fail_next is False)
        # tick siguiente: éxito y upsert
        await wait_until(lambda: len(net.scans) >= 2)
        machines = await db.list_machines()
        assert any(m.ip == "192.168.1.10" for m in machines)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_periodic_task_is_idempotent(db):
    """Segunda llamada a periodic_task no arranca otro loop (revisión 1.2)."""
    net = FakeNet([HostInfo(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:10")])
    svc = DiscoveryService(
        net=net, db=db, scan=ScanSettings(interval_seconds=1, ttl_seconds=60)
    )
    t1 = svc.periodic_task()
    t2 = svc.periodic_task()
    assert t1 is t2
    t1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t1
