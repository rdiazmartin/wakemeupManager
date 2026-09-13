---
title: '1-3-estado-online-offline-ttl'
type: 'feature'
created: '2026-09-13'
status: 'done'
route: 'oneshot'
baseline_commit: 'd2b0bc0'
review_loop_iteration: 0
context: []
---

## Intent

**Problem:** El inventario no tiene estado: no se sabe si una máquina está online u offline, y nadie actualiza el estado de forma periódica.

**Approach:** Añadir el estado online/offline por máquina con caducidad (TTL), conforme FR-2: el BE comprueba el estado cada TTL/2 (TTL default 60 s, configurable 15–300 s); un estado no refrescado en TTL se consulta como offline. Las máquinas en tailnet pero fuera del segmento local se reportan por su presencia en la LAN física (el estado se deriva del escaneo local + ping puntual, nunca de la tailnet).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Máquina online en LAN | host responde a ping | `state=online`, `checked_at` actualizado | — |
| Máquina offline en LAN | host no responde a ping | `state=offline`, `checked_at` actualizado | — |
| Estado caducado | pasado TTL sin comprobar (fallo del loop) | consulta → `offline` (no online caducado) | — |
| Rango no-LAN (tailnet) | máquina solo presente en tailnet | reportada según LAN física (escaneo local), no por tailnet | — |
| Inventario vacío | sin máquinas | `list_with_status` → `[]` | — |
| Máquina nueva | aparece en escaneo | entra con estado `offline` hasta primera comprobación | — |

## Tasks & Acceptance

**Execution:**
- [x] `backend/src/wakemeup/core/models.py` — añadir `state: str` ("online"/"offline") y `status_checked_at` a `Machine` (persistido) — la derivación `no_fiable` del AD-2/AD-10 llega con el alta (Epic 2)
- [x] `backend/src/wakemeup/adapters/db.py` — esquema: columnas `state TEXT NOT NULL DEFAULT 'offline'`, `status_checked_at TEXT`; `get_status(ip)`, `set_status(ip, state)`
- [x] `backend/src/wakemeup/services/status.py` — `StatusService`: `check_all()` (ping/ARP de cada máquina), `get_machine_status(ip)` (con TTL: caducado → offline), `check_cycle()` (cada TTL/2, FR-2)
- [x] `backend/tests/test_status.py` — tests: ciclo TTL/2, caducidad, consulta con estado, máquina nueva offline, inventario vacío

**Acceptance Criteria:**
- Given máquinas en inventario, when pasa TTL/2 segundos, then el estado se comprueba (online/offline por ping/ARP).
- Given un estado sin refrescar más de TTL, when se consulta, then se reporta offline (no online caducado).
- Given máquinas en tailnet fuera del segmento local, then se reportan por su presencia en la LAN física (el estado deriva del escaneo local + ping), no por tailnet.
- Given ninguna máquina en inventario, then la consulta devuelve lista vacía sin error.
- Given una máquina recién descubierta, then entra con estado offline hasta la primera comprobación.
- Given las ACs previas, then existe cobertura pytest del ciclo TTL/2, caducidad y consulta — requisito transversal de tests.
- Given la story completada, then commit + push obligatorio — requisito transversal del usuario.

## Implementation Notes

## Spec Change Log

## Review Triage Log
