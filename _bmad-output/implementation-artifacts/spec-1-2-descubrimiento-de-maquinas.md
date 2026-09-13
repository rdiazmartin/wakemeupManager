---
title: '1-2-descubrimiento-de-maquinas'
type: 'feature'
created: '2026-09-13'
status: 'done'
route: 'dispatch'
baseline_commit: '15e2f01'
review_loop_iteration: 0
context: []
---

## Intent

**Problem:** El BE aún no puede descubrir las máquinas de la LAN: no hay inventario, ni escaneo, ni persistencia.

**Approach:** Implementar el servicio de descubrimiento (FR-1/FR-3 parcial): adaptador `net` con ping binario (no-root) + lectura de `/proc/net/arp`, adaptador `db` con SQLite (`aiosqlite`) para el inventario, servicio `discovery` con singleflight, exclusión de interfaces propias y no-LAN, y disparo periódico configurable (10 min por defecto, FR-3). La carga de configuración `[scan]` vía pydantic-settings entra en esta story (pendiente declarado en 1.1). Los endpoints API (`POST /scan`, `GET /machines`) llegan en la story 1.4.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Escaneo de rango | rango `192.168.1.0/24` con 3 host activos | inventario con los 3 host: IP, MAC (si responde ARP), hostname (si resuelve) | — |
| Host apagado | host en rango que no responde | no entra en el inventario (upsert no destructivo) | — |
| Ping no-root sin permisos | binario ping sin capabilities | el adaptador loguea y continúa con solo `/proc/net/arp` | no bloquea el escaneo |
| IP propia del BE en rango | IP del BE (o tailnet/loopback) | excluida del inventario (AD-3) | — |
| Escaneo en curso (singleflight) | segundo `scan()` concurrente | el segundo espera o se omite; no hay escaneos duplicados concurrentes (AD-3) | — |
| ARP sin entrada | host responde ICMP pero no está en tabla ARP | se registra con `mac=None` (WOL requerirá MAC, se completa en alta/estado) | — |
| Config ausente | arranque sin `config.toml` | defaults de `[scan]`: range 192.168.1.0/24, interval 600 s, ttl 60 s | — |

## Tasks & Acceptance

**Execution:**
- [x] `backend/pyproject.toml` — añadir `aiosqlite>=0.22` y `ipaddress` (stdlib) ; dev: `pytest-asyncio` para tests async
- [x] `backend/src/wakemeup/adapters/net.py` — `ping_host(ip)`, `read_arp_table()`, `own_interfaces()` (excluye IPs propias incl. tailnet/loopback), `scan_range(cidr) -> list[HostInfo]`
- [x] `backend/src/wakemeup/adapters/db.py` — `init_db()`, `upsert_machine(ip, mac, hostname)`, `list_machines()`, esquema tabla `machines` (id PK, ip UNIQUE, mac, hostname, updated_at)
- [x] `backend/src/wakemeup/core/models.py` — dataclass `HostInfo(ip, mac, hostname)` y `Machine(ip, mac, hostname)` (forma del AD-2: identidad id+fingerprint llega en 1.4/2.x; aquí solo inventario)
- [x] `backend/src/wakemeup/config.py` — `Settings` pydantic-settings con sección `[scan]` (range, interval_seconds=600, ttl_seconds=60) + carga desde `backend/config/config.toml` si existe
- [x] `backend/src/wakemeup/services/discovery.py` — `DiscoveryService`: `scan()` con singleflight (asyncio lock), exclusión de propias (AD-3), upsert en DB, loop periódico configurable (FR-3)
- [x] `backend/tests/test_net_adapter.py` — dobles de ping/ARP: host activo, host apagado, IP propia excluida, ARP vacío
- [x] `backend/tests/test_discovery.py` — tests async: upsert, singleflight (2 scans concurrentes → 1), inventario persistido

**Acceptance Criteria:**
- Given una LAN con N máquinas activas y el BE ejecutando `DiscoveryService.scan()`, then el inventario (SQLite) contiene IP, MAC (si hay ARP) y hostname (si resuelve) de cada una — FR-1.
- Given el rango incluye la IP del BE, la tailnet y loopback, then ninguna IP/interface propia aparece en el inventario — AD-3.
- Given un host que deja de responder pero ya estaba inventariado, then sigue en el inventario (sin borrado; marcado offline en la story 1.3) — FR-3.
- Given dos llamadas concurrentes a `scan()`, then solo una ejecuta el escaneo efectivo (singleflight) — AD-3.
- Given el BE arranca sin `config.toml`, then usa los defaults `[scan]` y el loop periódico dispara cada 600 s.
- Given las ACs previas, then existe cobertura pytest (adaptador + servicio) — requisito transversal de tests.
- Given la story completada, then commit + push obligatorio — requisito transversal del usuario.

## Implementation Notes

- Reseñas (blind-hunter, edge-case, verification-gap) de la story 1.2: todas sus rutas de patch aplicadas y verificadas con 27/27 tests. Hallazgos de las 3 capas en el triage log; ninguno quedó diferido.
- **Transacción del upsert**: `_run` ahora envuelve el upsert en `begin/commit/rollback`; `Db.upsert_machine` ya **no** commitea (el commit lo decide quien gestiona la transacción). Esto cambia el contrato del adaptador: quedó documentado en el docstring del método.
- **Guarda de rango**: `scan_range` rechaza rangos con >65536 direcciones (evita gather de millones con un `10.0.0.0/8` mal configurado).
- **Degradación a ARP-only**: ahora solo si TODOS los pings fallan (antes: un solo error → degradaba). Semáforos acotados a >=1.
- **`own_interfaces`**: captura `FileNotFoundError` (degradación) y se probó el parseo real de `ip -o addr show`.
- **`periodic_task`** idempotente (segunda llamada devuelve la misma tarea).
- **Config**: prueba de fuente TOML real y de que env vence al fichero.
- Hostname lookup ahora solo con MAC presente (evita PTR para hosts sin MAC).

## Spec Change Log

## Review Triage Log

- blind-hunter `hostname serial` — **medium** — verificado: sí era serial. **patch**: PTR solo con MAC presente.
- blind-hunter `upsert no destructivo sin test / transacción` — **high** — verificado: upsert sin test de conservación e inventario parcial. **patch**: test + `begin/commit/rollback` en `_run`; `upsert_machine` sin commit propio.
- blind-hunter `own_interfaces sin degradación` — **high** — verificado: FileNotFoundError mataba el escaneo. **patch**: catch + retorno set().
- blind-hunter `DB path CWD-relative` — **medium** — **defer**: la ruta de la DB se integrará en la story 1.4/4.1 con la config `[db]` y el instalador; registrado en deferred-work.
- blind-hunter `sin tamaño máximo de rango` — **high** — verificado: 10.0.0.0/8 → gather de millones. **patch**: límite 65536.
- blind-hunter `degradación con un solo error` — **high** — verificado: EAGAIN individual descartaba todo. **patch**: degradación solo si TODOS fallan.
- blind-hunter `config TOML sin test + pytest.raises(Exception)` — **medium** — verificado. **patch**: test fuente TOML real, env>fichero, ValidationError.
- blind-hunter `logger muerto config.py` — **low** — **patch**: eliminado.
- blind-hunter `Db merge (CASE WHEN) y RuntimeError sin test` — **medium** — verificado. **patch**: test de conservación mac/hostname (cubre el CASE ELSE).
- edge-case `rango 8-15 sin guarda` — **high** — verificado (duplicado del `límite 65536` del blind-hunter, mismo hallazgo): parcheado.
- edge-case `FileNotFoundError ip` — **high** — duplicado blind-hunter `own_interfaces`: parcheado.
- edge-case `own_interfaces rc!=0 → set() silencioso` — **medium** — **defer** (riesgo asumido y anotado en implementación; se decidió no abortar el escaneo por una exclusión de interfaz degradada).
- edge-case `degradación parcial (1 ping falla)` — **high** — duplicado blind-hunter: parcheado (all()).
- edge-case `upsert sin transacción (parcial)` — **high** — duplicado blind-hunter: parcheado.
- edge-case `periodic_task doble` — **medium** — verificado: dos loops si se llama dos veces. **patch**: idempotente.
- edge-case `Semaphore(0)` — **low** — verificado: ValueError si <=0. **patch**: max(1).
- verification-gap `CASE ELSE sin test` — **medium** — verificado: 21/21 pasaba con el ELSE roto. **patch**: test conservación.
- verification-gap `TOML real sin test` — **high** — parcheado.
- verification-gap `own_interfaces real sin test` — **medium** — parcheado (parseo + rc!=0).
- verification-gap `except de loop sin test` — **medium** — parcheado (FlakyNet + wait_until).
- verification-gap `_in_flight falsa durante upsert` — **medium** — **defer** (verificado: sí ocurre; se resuelve con estado `running/scanning` en la story 1.4 cuando el endpoint lo consuma).

