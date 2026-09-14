"""Tests del adaptador net con dobles de ping/ARP (spec 1.2).

Cubren la matriz del spec: host activo, host apagado, IP propia excluida
(interfaces del BE, tailnet/loopback), ARP sin entrada para un ping OK,
rango no-LAN excluido y degradación a ARP-only cuando el ping no-root falla.
"""
import asyncio
import socket

import pytest

from wakemeup.adapters.net import Net, _PingOutcome

RANGE = "192.168.1.0/29"  # hosts .1–.6
OWN_IP = "192.168.1.3"


class FakeNet(Net):
    """Net sin IO: respuestas de ping y tabla ARP controladas por el test."""

    def __init__(self, alive: set[str], arp: dict[str, str] | None = None, own: set[str] | None = None) -> None:
        super().__init__(ping="fakeping", max_concurrent_pings=4)
        self._alive = set(alive)
        self._arp = dict(arp or {})
        self._own = own if own is not None else {"127.0.0.1", "10.0.0.2", OWN_IP}
        self.ping_calls: list[str] = []

    async def _ping_one(self, ip: str, sem) -> _PingOutcome:
        self.ping_calls.append(ip)
        return _PingOutcome(ip=ip, alive=ip in self._alive, error=None)

    async def read_arp_table(self) -> dict[str, str]:
        return dict(self._arp)

    async def own_interfaces(self) -> set[str]:
        return set(self._own)

    async def _resolve_hostname(self, ip: str) -> str | None:
        return None


@pytest.mark.asyncio
async def test_scan_resolves_hostname_for_arp_hosts():
    """Con MAC presente, se intenta el lookup PTR (spec: hostname si resuelve)."""

    class WithLookup(FakeNet):
        async def _resolve_hostname(self, ip: str) -> str | None:
            return "pc-maria" if ip == "192.168.1.1" else None

    net = WithLookup(alive={"192.168.1.1", "192.168.1.2"}, arp={"192.168.1.1": "aa:bb:cc:dd:ee:11"})
    hosts = await net.scan_range(RANGE)
    by_ip = {h.ip: h for h in hosts}
    assert by_ip["192.168.1.1"].hostname == "pc-maria"
    assert by_ip["192.168.1.2"].hostname is None


@pytest.mark.asyncio
async def test_scan_finds_active_hosts():
    net = FakeNet(alive={"192.168.1.1", "192.168.1.2"}, arp={"192.168.1.2": "aa:bb:cc:dd:ee:02"})
    hosts = await net.scan_range(RANGE)
    by_ip = {h.ip: h for h in hosts}
    assert set(by_ip) == {"192.168.1.1", "192.168.1.2"}
    assert by_ip["192.168.1.2"].mac == "aa:bb:cc:dd:ee:02"
    assert all(h.hostname is None or isinstance(h.hostname, str) for h in hosts)


@pytest.mark.asyncio
async def test_down_host_not_in_inventory():
    net = FakeNet(alive={"192.168.1.1"}, arp={})
    hosts = await net.scan_range(RANGE)
    assert "192.168.1.5" not in {h.ip for h in hosts}


@pytest.mark.asyncio
async def test_own_ip_excluded_even_if_alive():
    net = FakeNet(alive={"192.168.1.1", OWN_IP}, arp={OWN_IP: "de:ad:be:ef:00:03"})
    hosts = await net.scan_range(RANGE)
    assert OWN_IP not in {h.ip for h in hosts}
    assert OWN_IP not in net.ping_calls


@pytest.mark.asyncio
async def test_tailnet_and_loopback_ranges_skipped():
    net = FakeNet(alive={"100.100.100.5", "127.0.0.1"})
    assert await net.scan_range("100.64.0.0/30") == []
    assert await net.scan_range("127.0.0.0/30") == []


@pytest.mark.asyncio
async def test_alive_without_arp_entry_kept_with_mac_none():
    net = FakeNet(alive={"192.168.1.1"}, arp={})
    hosts = await net.scan_range(RANGE)
    by_ip = {h.ip: h for h in hosts}
    assert by_ip["192.168.1.1"].mac is None


@pytest.mark.asyncio
async def test_non_lan_range_excluded():
    net = FakeNet(alive={"203.0.113.9"}, arp={"203.0.113.9": "aa:00:11:22:33:44"})
    assert await net.scan_range("203.0.113.0/30") == []


@pytest.mark.asyncio
async def test_invalid_cidr_raises():
    net = FakeNet(alive=set())
    with pytest.raises(ValueError):
        await net.scan_range("not-a-cidr")


@pytest.mark.asyncio
async def test_ping_failure_falls_back_to_arp_only():
    class BrokenPing(FakeNet):
        async def _ping_one(self, ip: str, sem) -> _PingOutcome:
            return _PingOutcome(ip=ip, alive=False, error="ping rc=255")

    net = BrokenPing(alive=set(), arp={"192.168.1.2": "aa:bb:cc:dd:ee:02"})
    hosts = await net.scan_range(RANGE)
    assert [h.ip for h in hosts] == ["192.168.1.2"]
    assert hosts[0].mac == "aa:bb:cc:dd:ee:02"


@pytest.mark.asyncio
async def test_ping_host_individual():
    net = FakeNet(alive={"10.0.0.9"})
    assert await net.ping_host("10.0.0.9") is True
    assert await net.ping_host("10.0.0.8") is False


@pytest.mark.asyncio
async def test_read_arp_table_parses_only_complete_ethernet(monkeypatch):
    """Parsea el formato de /proc/net/arp: solo HW ethernet (0x1) + completa (0x2)."""
    arp_content = (
        "IP address       HW type     Flags     HW address            Mask     Device\n"
        "192.168.1.1      0x1         0x2       aa:bb:cc:dd:ee:11     *        eth0\n"
        "192.168.1.2      0x1         0x0       00:00:00:00:00:00     *        eth0\n"
        "192.168.1.3      0x6         0x2       aa:bb:cc:dd:ee:33     *        wlan0\n"
    )

    class FakeProc:
        async def communicate(self):
            return arp_content.encode(), b""

    async def fake_exec(*_args, **kwargs):
        proc = FakeProc()
        proc.returncode = 0
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    table = await Net().read_arp_table()
    assert table == {"192.168.1.1": "aa:bb:cc:dd:ee:11"}


@pytest.mark.asyncio
async def test_own_interfaces_parses_ip_addr_output(monkeypatch):
    """Verificación-gap: parseo real de `ip -o addr show` (formato completo)."""
    ip_output = (
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "2: eth0  inet 192.168.1.30/24 brd 192.168.1.255 scope global eth0\n"
        "3: tailscale0 inet 100.100.100.5/32 scope global tailscale0\n"
        "4: wlan0 inet6 fd7a:115c:a1e0::123/128 scope global wlan0\n"
    )

    class FakeProc:
        async def communicate(self):
            return ip_output.encode(), b""

    async def fake_exec(*_args, **kwargs):
        proc = FakeProc()
        proc.returncode = 0
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    own = await Net().own_interfaces()
    assert own == {"127.0.0.1", "192.168.1.30", "100.100.100.5", "fd7a:115c:a1e0::123"}

    # y parseo con rc != 0 → empty set (degradación documentada)
    class FakeProcRC:
        async def communicate(self):
            return b"", b"error"

    async def fake_exec_rc(*_args, **kwargs):
        proc = FakeProcRC()
        proc.returncode = 1
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec_rc)
    assert await Net().own_interfaces() == set()


@pytest.mark.asyncio
async def test_wol_interface_returns_ethernet_if_up(monkeypatch):
    """FR-5 parcial: eth0 con carrier (state UP) → 'eth0'."""
    link_output = (
        "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT group default\n"
        "2: eth0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP mode DEFAULT group default\n"
        "3: tailscale0: <POINTOPOINT,MULTICAST,NOARP,UP,LOWER_UP> mtu 1280 qdisc fq_codel state UNKNOWN group default\n"
    )

    class FakeProc:
        async def communicate(self):
            return link_output.encode(), b""

    async def fake_exec(*_args, **kwargs):
        proc = FakeProc()
        proc.returncode = 0
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    assert await Net().wol_interface() == "eth0"


@pytest.mark.asyncio
async def test_wol_interface_skips_carrierless_ethernet(monkeypatch):
    """eth0 sin cable (NO-CARRIER/DOWN) no se reporta aunque haya wlan0 UP."""
    link_output = (
        "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT group default\n"
        "2: eth0: <NO-CARRIER,BROADCAST,MULTICAST,UP> mtu 1500 qdisc fq_codel state DOWN mode DEFAULT group default\n"
        "3: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP mode DEFAULT group default\n"
    )

    class FakeProc:
        async def communicate(self):
            return link_output.encode(), b""

    async def fake_exec(*_args, **kwargs):
        proc = FakeProc()
        proc.returncode = 0
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    assert await Net().wol_interface() is None


@pytest.mark.asyncio
async def test_wol_interface_none_if_only_wireless_up(monkeypatch):
    """Solo wireless UP → None e informa warning sin romper el healthcheck."""
    link_output = (
        "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536 qdisc noqueue state UNKNOWN mode DEFAULT group default\n"
        "2: wlan0: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc fq_codel state UP mode DEFAULT group default\n"
    )

    class FakeProc:
        async def communicate(self):
            return link_output.encode(), b""

    async def fake_exec(*_args, **kwargs):
        proc = FakeProc()
        proc.returncode = 0
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    assert await Net().wol_interface() is None


@pytest.mark.asyncio
async def test_wol_interface_degrades_when_ip_missing(monkeypatch):
    """Binario `ip` ausente → None sin excepción (degradación)."""
    import builtins

    async def fake_exec(*_args, **kwargs):
        raise FileNotFoundError("ip")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    assert await Net().wol_interface() is None


@pytest.mark.asyncio
async def test_wol_interface_kills_lingering_procs_on_timeout(monkeypatch):
    """Timeout en `ip link` → mata el subproceso colgado y degrada a None."""
    killed = []

    class FakeSlowProc:
        async def communicate(self):
            await asyncio.sleep(30)  # nunca termina → timeout

        def kill(self):
            killed.append(True)

    async def fake_exec(*_args, **kwargs):
        proc = FakeSlowProc()
        proc.returncode = 1
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    assert await Net().wol_interface() is None
    assert killed == [True]


@pytest.mark.asyncio
async def test_wol_interface_degrades_on_ip_rc_nonzero(monkeypatch):
    """`ip -o link` con rc!=0 → None (degradación, nunca excepción)."""
    class FakeProc:
        async def communicate(self):
            return b"", b"error"

    async def fake_exec(*_args, **kwargs):
        proc = FakeProc()
        proc.returncode = 1
        return proc

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    assert await Net().wol_interface() is None


@pytest.mark.asyncio
async def test_wol_interface_degrades_on_oserror_not_filenotfound(monkeypatch):
    """PermissionError (u otro OSError) al ejecutar `ip` → None sin excepción."""
    async def fake_exec(*_args, **kwargs):
        raise PermissionError("ip sin permiso")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    assert await Net().wol_interface() is None


# --- WOL: send_wol (epic 2, AC 2.2 / FR-4) ---


def test_normalize_mac_variants():
    """12 hex o 17 con `:`/`-` → 6 bytes; separadores mixtos también valen."""
    assert Net.normalize_mac("AA:BB:CC:DD:EE:FF") == b"\xaa\xbb\xcc\xdd\xee\xff"
    assert Net.normalize_mac("aabbccddeeff") == b"\xaa\xbb\xcc\xdd\xee\xff"
    assert Net.normalize_mac("AA-BB-CC-DD-EE-FF") == b"\xaa\xbb\xcc\xdd\xee\xff"


@pytest.mark.parametrize(
    "mac",
    ["", "zz:bb:cc:dd:ee:ff", "aa:bb:cc", "aabbccddeff", "aa:bb:cc:dd:ee:ff:11"],
)
def test_normalize_mac_invalid(mac):
    with pytest.raises(ValueError):
        Net.normalize_mac(mac)


def test_normalize_mac_rejects_multicast_and_zero():
    with pytest.raises(ValueError):
        Net.normalize_mac("01:00:00:00:00:00")  # multicast (bit LSB)
    with pytest.raises(ValueError):
        Net.normalize_mac("00:00:00:00:00:00")  # cero


def test_magic_packet_structure():
    """6×0xFF + MAC×16 como bytes (spec 2.2)."""
    mac = Net.normalize_mac("aa:bb:cc:dd:ee:01")
    magic = b"\xff" * 6 + mac * 16
    assert magic == b"\xff" * 6 + b"\xaa\xbb\xcc\xdd\xee\x01" * 16
    assert len(magic) == 6 + 6 * 16


@pytest.mark.asyncio
async def test_send_wol_sends_udp_broadcast(monkeypatch):
    """Un único datagrama UDP al broadcast con SO_BROADCAST (puerto 9)."""
    sent: list[tuple[bytes, tuple[str, int]]] = []
    setopts: list[tuple[int, int]] = []

    class FakeSock:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def setsockopt(self, level, opt, value):
            setopts.append((level, opt))
            if opt == socket.SO_BINDTODEVICE:
                raise OSError("sin CAP_NET_RAW")  # degradación del bind

        def sendto(self, data, addr):
            sent.append((data, addr))

    monkeypatch.setattr("socket.socket", lambda *a, **kw: FakeSock())
    net = Net()

    class EthNet(net.__class__):
        async def wol_interface(self):
            return "eth0"

    iface = await EthNet().send_wol("aa:bb:cc:dd:ee:11")
    assert iface == "eth0"
    assert len(sent) == 1
    data, addr = sent[0]
    assert data == b"\xff" * 6 + b"\xaa\xbb\xcc\xdd\xee\x11" * 16
    assert addr == ("255.255.255.255", 9)
    assert (socket.SOL_SOCKET, socket.SO_BROADCAST) in setopts


@pytest.mark.asyncio
async def test_send_wol_falls_back_to_port_7(monkeypatch):
    """Si falla el puerto 9, fallback al 7 (nota de implementación)."""
    sent: list[tuple[bytes, tuple[str, int]]] = []

    class FakeSock:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def setsockopt(self, level, opt, value):
            pass

        def sendto(self, data, addr):
            if addr[1] == 9:
                raise OSError("EACCES broadcast")
            sent.append((data, addr))

    monkeypatch.setattr("socket.socket", lambda *a, **kw: FakeSock())
    net = Net()

    class EthNet(net.__class__):
        async def wol_interface(self):
            return "eth0"

    iface = await EthNet().send_wol("aa:bb:cc:dd:ee:11")
    assert iface == "eth0"
    assert sent and sent[0][1][1] == 7


@pytest.mark.asyncio
async def test_send_wol_without_ethernet_raises_runtime_error(monkeypatch):
    """Sin interfaz Ethernet emisora → RuntimeError (el servicio degrada a éxito)."""
    net = Net()

    class NoEth(net.__class__):
        async def wol_interface(self):
            return None

    with pytest.raises(RuntimeError):
        await NoEth().send_wol("aa:bb:cc:dd:ee:11")


@pytest.mark.asyncio
async def test_send_wol_invalid_mac_raises_value_error(monkeypatch):
    net = Net()
    with pytest.raises(ValueError):
        await net.send_wol("zz:zz:zz:zz:zz:zz")
