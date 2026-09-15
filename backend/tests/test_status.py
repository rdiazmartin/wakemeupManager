"""Tests de estado online/offline (spec 1.3) y del healthcheck (AD-1).

Cubren la matriz del spec 1.3: ciclo cada TTL/2, caducidad del estado
(caducado → offline), consulta con estado persistido, máquina nueva offline
hasta primera comprobación e inventario vacío sin error.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from wakemeup import __version__
from wakemeup.adapters.db import Db
from wakemeup.adapters.net import Net
from wakemeup.api import app
from wakemeup.config import ScanSettings
from wakemeup.core.models import HostInfo
from wakemeup.services.status import StatusService


def test_status_ok() -> None:
    client = TestClient(app)
    resp = client.get("/api/v1/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


class FakeNet(Net):
    """Net sin IO: respuestas de ping controladas por el test."""

    def __init__(self, alive: set[str]) -> None:
        super().__init__(ping="fakeping")
        self.alive = set(alive)
        self.ping_calls: list[str] = []

    async def ping_host(self, ip: str) -> bool:
        self.ping_calls.append(ip)
        return ip in self.alive

    async def read_arp_table(self) -> dict[str, str]:
        return {}


@pytest.fixture
async def db(tmp_path):
    db = Db(path=tmp_path / "test.db")
    await db.init_db()
    yield db
    await db.close()


async def _seed(db: Db, hosts: list[HostInfo]) -> None:
    for host in hosts:
        await db.begin()
        await db.upsert_machine(ip=host.ip, mac=host.mac, hostname=host.hostname)
        await db.commit()


def _checked_at(seconds_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


async def _force_checked_at(db: Db, ip: str, stamp: str) -> None:
    """Escribe a mano la marca temporal del estado (el loop no espera en tests)."""
    await db._conn.execute(
        "UPDATE machines SET status_checked_at = ? WHERE ip = ?", (stamp, ip)
    )
    await db.commit()


@pytest.mark.asyncio
async def test_check_all_marks_online_and_offline(db):
    net = FakeNet(alive={"192.168.1.10"})
    svc = StatusService(net=net, db=db, scan=ScanSettings())
    await _seed(db, [HostInfo(ip="192.168.1.10"), HostInfo(ip="192.168.1.11")])

    checked = await svc.check_all()
    assert checked == 2
    assert set(net.ping_calls) == {"192.168.1.10", "192.168.1.11"}
    assert await svc.get_machine_status("192.168.1.10") == "online"
    assert await svc.get_machine_status("192.168.1.11") == "offline"


@pytest.mark.asyncio
async def test_check_all_updates_checked_at(db):
    """Matriz spec: máquina offline → state=offline con checked_at actualizado."""
    net = FakeNet(alive={"192.168.1.10"})
    svc = StatusService(net=net, db=db, scan=ScanSettings())
    await _seed(db, [HostInfo(ip="192.168.1.11")])

    assert await svc.check_all() == 1
    assert await svc.get_machine_status("192.168.1.11") == "offline"
    state, checked_at = await db.get_status("192.168.1.11")
    assert state == "offline"
    assert checked_at is not None


@pytest.mark.asyncio
async def test_online_state_within_ttl_is_reported(db):
    svc = StatusService(net=FakeNet(set()), db=db, scan=ScanSettings(ttl_seconds=300))
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    await db.begin()
    await db.set_status("192.168.1.10", "online")
    await db.commit()
    await _force_checked_at(db, "192.168.1.10", _checked_at(100))

    assert await svc.get_machine_status("192.168.1.10") == "online"


@pytest.mark.asyncio
async def test_stale_online_state_reports_offline(db):
    """Matriz spec: estado caducado → offline (nunca online caducado)."""
    svc = StatusService(net=FakeNet(set()), db=db, scan=ScanSettings(ttl_seconds=60))
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    await db.begin()
    await db.set_status("192.168.1.10", "online")
    await db.commit()
    await _force_checked_at(db, "192.168.1.10", _checked_at(120))

    assert await svc.get_machine_status("192.168.1.10") == "offline"


@pytest.mark.asyncio
async def test_no_status_returns_none(db):
    svc = StatusService(net=FakeNet(set()), db=db, scan=ScanSettings())
    assert await svc.get_machine_status("192.168.1.200") is None


@pytest.mark.asyncio
async def test_new_machine_offline_until_first_check(db):
    """Matriz spec: máquina recién descubierta entra offline (default de columna)."""
    svc = StatusService(net=FakeNet(set()), db=db, scan=ScanSettings())
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    assert await svc.get_machine_status("192.168.1.10") == "offline"


@pytest.mark.asyncio
async def test_empty_inventory_returns_no_error(db):
    svc = StatusService(net=FakeNet(set()), db=db, scan=ScanSettings())
    assert await svc.list_with_status() == []
    assert await svc.check_all() == 0


@pytest.mark.asyncio
async def test_list_with_status_applies_ttl(db):
    svc = StatusService(net=FakeNet(set()), db=db, scan=ScanSettings(ttl_seconds=60))
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    await db.begin()
    await db.set_status("192.168.1.10", "online")
    await db.commit()
    await _force_checked_at(db, "192.168.1.10", _checked_at(120))

    machines = await svc.list_with_status()
    assert len(machines) == 1
    assert machines[0].state == "offline"
    persisted = await db.get_status("192.168.1.10")
    assert persisted[0] == "online"  # la derivación no destruye la BD


@pytest.mark.asyncio
async def test_check_cycle_checks_each_ttl_half(db):
    net = FakeNet(alive={"192.168.1.10"})
    svc = StatusService(net=net, db=db, scan=ScanSettings(ttl_seconds=16))
    await _seed(db, [HostInfo(ip="192.168.1.10")])

    task = svc.check_cycle(interval_seconds=1)
    try:
        # TTL 16 s → comprobación cada 8 s; con tick acelerado a 1 s, la
        # primera comprobación ocurre al arrancar y la segunda en el tick 1.
        deadline = asyncio.get_running_loop().time() + 4
        while asyncio.get_running_loop().time() < deadline:
            if len(net.ping_calls) >= 2:
                break
            await asyncio.sleep(0.1)
        assert len(net.ping_calls) >= 2
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_check_cycle_is_idempotent(db):
    svc = StatusService(net=FakeNet(set()), db=db, scan=ScanSettings(ttl_seconds=16))
    t1 = svc.check_cycle()
    t2 = svc.check_cycle()
    assert t1 is t2
    t1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await t1


@pytest.mark.asyncio
async def test_stop_cancels_check_cycle(db):
    """stop() (verification-gap 1.4): cancela el loop real de estado."""
    net = FakeNet(alive={"192.168.1.10"})
    svc = StatusService(net=net, db=db, scan=ScanSettings(ttl_seconds=16))
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    task = svc.check_cycle(interval_seconds=1)
    await asyncio.sleep(0.2)
    assert len(net.ping_calls) >= 1

    svc.stop()
    await asyncio.sleep(0)  # cede el ciclo al event loop para entregar el cancel
    assert task.cancelled()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_check_all_rollback_on_mid_sweep_failure(db):
    """Triage 1.3: un ping que falla a mitad del barrido revierte el lote
    entero y no deja escrituras parciales."""
    class FlakyNet(FakeNet):
        def __init__(self) -> None:
            super().__init__(alive={"192.168.1.10", "192.168.1.11"})

        async def ping_host(self, ip: str) -> bool:
            if ip == "192.168.1.11":
                raise RuntimeError("ping reventado")
            return True

    svc = StatusService(net=FlakyNet(), db=db, scan=ScanSettings(ttl_seconds=60))
    await _seed(db, [HostInfo(ip="192.168.1.10"), HostInfo(ip="192.168.1.11")])

    with pytest.raises(RuntimeError):
        await svc.check_all()
    # rollback efectivo: ninguna escritura de estado del lote persistió
    # (las filas existen por el seed, pero con status_checked_at == None)
    for ip in ("192.168.1.10", "192.168.1.11"):
        state, checked_at = await db.get_status(ip)
        assert checked_at is None
        assert state == "offline"


@pytest.mark.asyncio
async def test_invalid_timestamp_counts_as_stale(db):
    """Triage 1.3: una marca ilegible se trata como caducada (docstring: 'ilegible' → offline)."""
    svc = StatusService(net=FakeNet(set()), db=db, scan=ScanSettings(ttl_seconds=60))
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    await db.begin()
    await db.set_status("192.168.1.10", "online")
    await db.commit()
    await _force_checked_at(db, "192.168.1.10", "no-es-una-fecha")

    assert await svc.get_machine_status("192.168.1.10") == "offline"
    machines = await svc.list_with_status()
    assert machines[0].state == "offline"


@pytest.mark.asyncio
async def test_check_cycle_survives_failing_check(db):
    """Triage 1.3: un check_all que falla no mata el loop; el siguiente tick reintenta."""
    class FlakyNet(FakeNet):
        def __init__(self) -> None:
            super().__init__(alive={"192.168.1.10"})
            self.fail_next = True

        async def ping_host(self, ip: str) -> bool:
            self.ping_calls.append(ip)
            if self.fail_next:
                self.fail_next = False
                raise RuntimeError("ping roto temporalmente")
            return True

    net = FlakyNet()
    svc = StatusService(net=net, db=db, scan=ScanSettings(ttl_seconds=16))
    await _seed(db, [HostInfo(ip="192.168.1.10")])

    async def wait_until(predicate, timeout_s: float = 3.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.05)
        raise AssertionError("condición no alcanzada a tiempo")

    task = svc.check_cycle(interval_seconds=1)
    try:
        await wait_until(lambda: net.fail_next is False)
        await wait_until(lambda: len(net.ping_calls) >= 2)
        status = await db.get_status("192.168.1.10")
        assert status is not None and status[0] == "online"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_check_all_never_overwrites_no_fiable_state(db):
    """Regresión epic 2: una máquina `no_fiable` (fingerprint mismatch, AD-2/AD-10)
    SOBREVIVE a los barridos del loop de estado — set_status nunca la machaca
    con online/offline (guard `state != 'no_fiable'` en _UPSERT_STATUS)."""
    svc = StatusService(net=FakeNet(alive={"192.168.1.10"}), db=db, scan=ScanSettings(ttl_seconds=300))
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    await db.begin()
    await db.set_managed(1, "SHA256:abcd", "maria")  # alta: deja state intacto
    await db.set_no_fiable(1)  # mismatch de fingerprint (el único origen de no_fiable)
    await db.commit()
    # El alta NO marca no_fiable (AC 2.1: operable); lo hace el mismatch.
    machine = await db.get_by_id(1)
    assert machine.fingerprint == "SHA256:abcd"
    assert (await db.get_status("192.168.1.10"))[0] == "no_fiable"

    # Barrido con la máquina viva (y otro con la máquina muerta): el estado
    # persistido no_fiable debe conservarse en ambos casos.
    await svc.check_all()
    state, _ = await db.get_status("192.168.1.10")
    assert state == "no_fiable"

    svc2 = StatusService(net=FakeNet(set()), db=db, scan=ScanSettings(ttl_seconds=300))
    await svc2.check_all()
    state, _ = await db.get_status("192.168.1.10")
    assert state == "no_fiable"


@pytest.mark.asyncio
async def test_check_all_emits_transition_with_periodic_origin(db):
    """3.3: una transición real de estado se persiste y emite con origen periodic."""
    from wakemeup.services.events import EventBus

    bus = EventBus()
    net = FakeNet(alive={"192.168.1.10"})
    svc = StatusService(net=net, db=db, scan=ScanSettings(ttl_seconds=60), events=bus)
    await _seed(db, [HostInfo(ip="192.168.1.10"), HostInfo(ip="192.168.1.11")])
    # La .11 estaba online y ahora no responde → transición real a offline.
    await db.begin()
    await db.set_status("192.168.1.11", "online", origin="periodic")
    await db.commit()

    async with bus.subscribe() as queue:
        await svc.check_all()
        events = [queue.get_nowait() for _ in range(queue.qsize())]
    emitted = {(e.machine, e.origin, e.type) for e in events}
    assert ("192.168.1.10", "periodic", "machine_online") in emitted
    assert ("192.168.1.11", "periodic", "machine_offline") in emitted
    # El evento no transporta credenciales ni claves.
    for e in events:
        assert set(e.to_payload()) == {"type", "machine", "timestamp", "origin"}
    # La transición quedó registrada en FR-11 y con last_origin persistido.
    assert await db.activity_stats() == [
        ("status", "offline", 1), ("status", "online", 1)
    ]
    machine = await db.get_by_id(1)
    assert machine.last_origin == "periodic"
    assert machine.last_change_at is not None


@pytest.mark.asyncio
async def test_sweep_transition_preserves_recent_mcp_origin(db):
    """3.5: el desenlace de una acción del agente conserva `origin='mcp'`.

    El wake del MCP fija `last_origin='mcp'` (estado aún offline); cuando el
    barrido detecta la transición offline→online, debe conservar el origen
    `mcp` en la fila, el evento y la actividad — si no, el fallback de polling
    de la app nunca notificaría (FR-18).
    """
    from wakemeup.services.events import EventBus

    bus = EventBus()
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    await db.begin()
    await db.set_last_change(1, "mcp")
    await db.commit()

    svc = StatusService(
        net=FakeNet(alive={"192.168.1.10"}), db=db, scan=ScanSettings(ttl_seconds=60),
        events=bus,
    )
    async with bus.subscribe() as queue:
        await svc.check_all()
        event = queue.get_nowait()

    assert event.type == "machine_online"
    assert event.origin == "mcp"
    machine = await db.get_by_id(1)
    assert machine.state == "online"
    assert machine.last_origin == "mcp"
    assert machine.last_change_at is not None
    # La actividad también queda con canal mcp (FR-11).
    assert ("status", "online", 1) in [
        (op, res, c) for op, res, c in await db.activity_stats()
    ]
    entries = await db.list_activity(limit=5)
    assert entries[-1].channel == "mcp"


@pytest.mark.asyncio
async def test_check_all_skips_transition_marked_no_fiable_mid_sweep(db):
    """Regresión: si la fila pasa a `no_fiable` entre la lectura y el UPDATE
    (p. ej. un shutdown con mismatch de fingerprint), `set_status` afecta a 0
    filas y NO debe registrarse actividad ni emitirse evento de esa transición."""
    from wakemeup.services.events import EventBus

    class RacingDb(Db):
        """Marca `no_fiable` la máquina leída justo antes del UPDATE del barrido."""

        def __init__(self, path) -> None:
            super().__init__(path)
            self.race = True

        async def list_machines(self):
            stale = await super().list_machines()
            if self.race:
                self.race = False
                await self.begin()
                await self.set_no_fiable(stale[0].id, origin="api")
                await self.commit()
            return stale

    racing = RacingDb(path=db._path)
    await racing.init_db()
    bus = EventBus()
    await _seed(racing, [HostInfo(ip="192.168.1.10")])

    svc = StatusService(
        net=FakeNet(alive={"192.168.1.10"}), db=racing,
        scan=ScanSettings(ttl_seconds=60), events=bus,
    )
    async with bus.subscribe() as queue:
        await svc.check_all()
        assert queue.qsize() == 0  # sin evento espurio

    # Sin actividad de estado y la fila conserva `no_fiable`.
    assert await racing.activity_stats() == []
    machine = await racing.get_by_id(1)
    assert machine.state == "no_fiable"
    await racing.close()


@pytest.mark.asyncio
async def test_sweep_transition_uses_periodic_when_mcp_stamp_stale(db):
    """3.5: un `last_origin='mcp'` caducado no se conserva (→ `periodic`)."""
    from wakemeup.services.events import EventBus

    bus = EventBus()
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    await db.begin()
    await db.set_last_change(1, "mcp")
    # Envejece la marca más allá de la ventana (2×intervalo, mín. 120 s).
    await db._conn.execute(
        "UPDATE machines SET last_change_at = ? WHERE id = 1",
        (_checked_at(600),),
    )
    await db.commit()

    svc = StatusService(
        net=FakeNet(alive={"192.168.1.10"}), db=db, scan=ScanSettings(ttl_seconds=60),
        events=bus,
    )
    async with bus.subscribe() as queue:
        await svc.check_all()
        event = queue.get_nowait()

    assert event.origin == "periodic"
    machine = await db.get_by_id(1)
    assert machine.last_origin == "periodic"


@pytest.mark.asyncio
async def test_periodic_sweep_preserves_mcp_last_origin_without_change(db):
    """3.5: un barrido sin transición no pisa el `last_origin='mcp'` previo.

    El fallback de notificación por polling lee `last_origin`; un sweep
    periódico que no cambia el estado no debe borrar el origen del cambio real.
    """
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    await db.begin()
    await db.set_status("192.168.1.10", "online", origin="mcp")
    await db.commit()
    svc = StatusService(
        net=FakeNet(alive={"192.168.1.10"}), db=db, scan=ScanSettings(ttl_seconds=300)
    )
    await svc.check_all()  # online -> online: sin transición
    machine = await db.get_by_id(1)
    assert machine.last_origin == "mcp"


@pytest.mark.asyncio
async def test_check_all_does_not_emit_without_change(db):
    """3.3: sin cambio de estado (previo == nuevo) no se emite ni registra nada."""
    from wakemeup.services.events import EventBus

    bus = EventBus()
    net = FakeNet(alive={"192.168.1.10"})
    svc = StatusService(net=net, db=db, scan=ScanSettings(ttl_seconds=60), events=bus)
    await _seed(db, [HostInfo(ip="192.168.1.10")])

    async with bus.subscribe() as queue:
        await svc.check_all()  # offline -> online (transición)
        queue.get_nowait()
        await svc.check_all()  # online -> online (sin cambio)
        assert queue.qsize() == 0


@pytest.mark.asyncio
async def test_event_without_subscribers_is_dropped(db):
    """3.3: sin suscriptores el evento se omite (v1 sin cola de entrega)."""
    from wakemeup.services.events import EventBus

    bus = EventBus()
    net = FakeNet(alive={"192.168.1.10"})
    svc = StatusService(net=net, db=db, scan=ScanSettings(ttl_seconds=60), events=bus)
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    await svc.check_all()  # no debe romper sin suscriptores
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_set_no_fiable_persists_origin_and_change_at(db):
    """3.3: `set_no_fiable(origin='mcp')` persiste estado, origen y marca."""
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    await db.begin()
    await db.set_no_fiable(1, origin="mcp")
    await db.commit()
    machine = await db.get_by_id(1)
    assert machine.state == "no_fiable"
    assert machine.last_origin == "mcp"
    assert machine.last_change_at is not None


@pytest.mark.asyncio
async def test_set_last_change_persists_origin_for_polling(db):
    """3.5: `set_last_change` persiste el origen del último cambio (FR-18)."""
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    await db.begin()
    await db.set_last_change(1, "mcp")
    await db.commit()
    machine = await db.get_by_id(1)
    assert machine.last_origin == "mcp"
    assert machine.last_change_at is not None
    assert machine.state == "offline"  # no altera el estado


@pytest.mark.asyncio
async def test_set_managed_keeps_machine_operable(db):
    """Regresión del alta real: `set_managed` NO deja la máquina en no_fiable.

    El alta debe dejar la máquina operable (AC 2.1 "managed: true y puede
    apagarse"); `no_fiable` es solo para el mismatch de fingerprint del
    shutdown. Antes, el alta marcaba no_fiable y —con el guard del loop de
    estado— la máquina quedaba atascada sin acciones en la app.
    """
    await _seed(db, [HostInfo(ip="192.168.1.10")])
    await db.begin()
    await db.set_managed(1, "SHA256:abcd", "maria")
    await db.commit()
    machine = await db.get_by_id(1)
    assert machine.fingerprint == "SHA256:abcd"
    assert machine.state != "no_fiable"
