"""Adaptador de red: descubrimiento sin root (AD-3).

Técnica verificada en la arquitectura: binario `ping` del distro + lectura de
`/proc/net/arp` (mundo-readable), sin capabilities ni paquetes extra. Si el
binario ping falla por permisos, el escaneo continúa de forma degradada con
solo la tabla ARP (nunca bloquea).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass

from wakemeup.core.models import HostInfo

logger = logging.getLogger(__name__)

_PING = "ping"
# -4 fuerza IPv4 (bookworm responde con doble familia en hosts dual-stack y el
# contador de hosts vivos se duplicaría); -w 1 = deadline total de 1 s; -W 1 = espera por respuesta.
_PING_FLAGS = ("-4", "-c", "1", "-W", "1", "-w", "1")
_MAX_CONCURRENT_PINGS = 256
_MAX_CONCURRENT_LOOKUPS = 8
_HOSTNAME_TIMEOUT = 1.0
_SUBPROCESS_TIMEOUT = 5.0

# Redes consideradas "propias del BE": loopback físico y subred de la tailnet
# (AD-3: excluir tailnet y loopback, y cualquier dirección no-LAN).
_OWN_SUBNETS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)


@dataclass(frozen=True)
class _PingOutcome:
    ip: str
    alive: bool
    error: str | None = None


class Net:
    """Primitivas de red del descubrimiento (ping binario + /proc/net/arp)."""

    def __init__(
        self,
        ping: str = _PING,
        ping_flags: tuple[str, ...] = _PING_FLAGS,
        own_subnets: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = _OWN_SUBNETS,
        max_concurrent_pings: int = _MAX_CONCURRENT_PINGS,
        max_concurrent_lookups: int = _MAX_CONCURRENT_LOOKUPS,
    ) -> None:
        self._ping = ping
        self._ping_flags = ping_flags
        self._own_subnets = own_subnets
        # Guardas de construcción (revisión 1.2): Semaphore(<=0) lanza ValueError.
        max_concurrent_pings = max(max_concurrent_pings, 1)
        max_concurrent_lookups = max(max_concurrent_lookups, 1)
        self._sem_pings = asyncio.Semaphore(max_concurrent_pings)
        self._sem_lookups = asyncio.Semaphore(max_concurrent_lookups)

    async def ping_host(self, ip: str) -> bool:
        """Sonda ICMP individual; `False` ante cualquier fallo del binario."""
        return (await self._ping_one(ip, self._sem_pings)).alive

    async def _ping_one(self, ip: str, sem: asyncio.Semaphore) -> _PingOutcome:
        async with sem:
            try:
                proc = await asyncio.create_subprocess_exec(
                    self._ping, *self._ping_flags, ip,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            except OSError as exc:
                return _PingOutcome(ip, alive=False, error=f"ping no ejecutable: {exc}")
            try:
                returncode = await asyncio.wait_for(proc.wait(), timeout=8)
            except (asyncio.TimeoutError, ProcessLookupError):
                proc.kill()
                return _PingOutcome(ip, alive=False, error="timeout")
            if returncode == 0:
                return _PingOutcome(ip, alive=True)
            # 1 = sin respuesta (host apagado); 2 = inalcanzable. Cualquier otro
            # returncode (p. ej. 255) = problema del binario (sin capabilities
            # no-root, flags no soportados...): se reporta como error.
            if returncode in (1, 2):
                return _PingOutcome(ip, alive=False)
            logger.warning("ping %s: returncode %s", ip, returncode)
            return _PingOutcome(ip, alive=False, error=f"ping rc={returncode}")

    async def read_arp_table(self) -> dict[str, str]:
        """IP → MAC para las entradas completas de `/proc/net/arp`."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "/bin/cat", "/proc/net/arp",
                stdout=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.warning("/proc/net/arp no existe en esta plataforma; sin MACs")
            return {}
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_SUBPROCESS_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error("timeout leyendo /proc/net/arp")
            return {}
        entries: dict[str, str] = {}
        for line in stdout.decode(errors="replace").splitlines()[1:]:
            parts = line.split()
            if len(parts) < 4:
                continue
            ip, hw_type, flags, mac = parts[0], parts[1], parts[2], parts[3]
            # HW type ethernet (0x1) + completa (0x2): "incomplete" (0x0) y no-ethernet se ignoran.
            if hw_type == "0x1" and flags == "0x2":
                entries[ip] = mac
        return entries

    async def own_interfaces(self) -> set[str]:
        """IPs de las interfaces del BE (`ip -o addr`): a excluir del inventario (AD-3).

        Degrada a conjunto vacío si el binario no está (el escaneo continúa;
        la auto-exclusión por interfaz se pierde pero no se aborta).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip", "-o", "addr", "show",
                stdout=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.warning("binario `ip` no disponible; sin auto-exclusión por interfaz")
            return set()
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_SUBPROCESS_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error("timeout en `ip addr`")
            return set()
        if proc.returncode != 0:
            logger.error("`ip addr` falló: rc=%s", proc.returncode)
            return set()
        own: set[str] = set()
        for line in stdout.decode(errors="replace").splitlines():
            for chunk in line.split()[3:]:
                if "/" not in chunk:
                    continue
                addr, _ = chunk.split("/", 1)
                try:
                    own.add(str(ipaddress.ip_address(addr)))
                except ValueError:
                    continue
        return own

    async def scan_range(self, cidr: str) -> list[HostInfo]:
        """Escanea el rango y devuelve los hosts vivos, sin IPs propias ni no-LAN.

        Degradación (AD-3): si el binario ping no cumple su contrato (p. ej.
        sin capabilities no-root), el escaneo se basa solo en `/proc/net/arp`.
        """
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError as exc:
            raise ValueError(f"rango inválido: {cidr!r}") from exc

        if network.version != 4 or not (8 <= network.prefixlen <= 30):
            logger.warning("rango no soportado para escaneo: %s", cidr)
            return []
        # Guarda de tamaño (revisión 1.2): un rango demasiado amplio (p. ej.
        # 10.0.0.0/8) materializaría un gather de millones de coroutines.
        if network.num_addresses > 65536:
            logger.warning("rango excesivo, se omite escaneo (límite 65536 direcciones): %s", cidr)
            return []
        if self._is_own_network(network):
            logger.warning("el rango configura la red propia del BE (loopback/tailnet); escaneo vacío: %s", cidr)
            return []
        if not network.is_private and not network.is_link_local:
            logger.warning("rango no-LAN excluido (AD-3): %s", cidr)
            return []

        own = await self.own_interfaces()
        candidates = [str(ip) for ip in network.hosts() if str(ip) not in own]

        outcomes = await asyncio.gather(
            *(self._ping_one(ip, self._sem_pings) for ip in candidates)
        )
        arp = await self.read_arp_table()

        # Degradación solo si TODOS fallan (el binario no cumple su contrato);
        # un fallo puntual (p. ej. EAGAIN al reventar EMFILE) no descarta los
        # resultados válidos de los demás hosts (revisión 1.2).
        if all(o.error for o in outcomes):
            degraded = True
            logger.warning(
                "el binario ping falló en todos los hosts; el escaneo continúa con solo /proc/net/arp",
            )
        else:
            degraded = False

        if degraded:
            alive: list[str] = []
            for ip in arp:
                if ip in own:
                    continue
                try:
                    if ipaddress.ip_address(ip) in network:
                        alive.append(ip)
                except ValueError:
                    continue
        else:
            alive = [o.ip for o in outcomes if o.alive]

        hosts: list[HostInfo] = []
        for ip in alive:
            mac = arp.get(ip)
            if not mac:
                hosts.append(HostInfo(ip=ip))
                continue
            hostname = await self._resolve_hostname(ip)
            hosts.append(HostInfo(ip=ip, mac=mac, hostname=hostname))
        return hosts

    def _is_own_network(self, network: ipaddress.IPv4Network) -> bool:
        return any(
            network.version == subnet.version and network.subnet_of(subnet)
            for subnet in self._own_subnets
        )

    async def _resolve_hostname(self, ip: str) -> str | None:
        """PTR lookup con timeout corto; `None` si no hay registro."""
        loop = asyncio.get_running_loop()

        def _lookup() -> str | None:
            try:
                return socket.gethostbyaddr(ip)[0]
            except (socket.herror, socket.gaierror, OSError):
                return None

        async with self._sem_lookups:
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(None, _lookup), timeout=_HOSTNAME_TIMEOUT
                )
            except asyncio.TimeoutError:
                return None
