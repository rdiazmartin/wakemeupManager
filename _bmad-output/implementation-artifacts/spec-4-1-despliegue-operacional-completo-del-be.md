---
title: '4-1-despliegue-operacional-completo-del-be'
type: 'feature'
created: '2026-09-15'
status: 'review'
baseline_commit: '6ba9cfa9823acb120f32b5e3bb82e4e44259f762'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-4-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** El BE no tiene un despliegue operacional reproducible: la unidad `systemd` apunta a rutas y hosts con `TODO`, no existe instalador idempotente, y uvicorn arranca fijo en `127.0.0.1` (la app y el agente MCP no pueden alcanzarlo por la tailnet). Además `[api] bind_hosts/port` se parsea pero no gobierna el bind real.

**Approach:** Añadir un runner propio de uvicorn (`wakemeup.server`) que bindee los hosts de `[api] bind_hosts` (tailnet + loopback) y tolere que la interfaz tailnet aparezca tarde; un instalador idempotente en Python (`wakemeup.install`, con shim `deploy/install.sh`) que crea usuario dedicado, par Ed25519, config y unidad, sincroniza con `uv` y habilita el servicio; y la unidad `systemd` final con `Restart=always`.

**Decisions (renegociadas 2026-09-15):**
- **Q1 = AUTO:** el instalador detecta la IP tailnet (`tailscale ip -4`) y el `DNSName` (`tailscale status --json`) y escribe `[api] bind_hosts=[<tailnet>, "127.0.0.1"]` y `extra_allowed_hosts=[<dnsname>]`; sin `tailscale` en el host → aviso y config solo-loopback (el operador la completa).
- **Q2 = IMPLEMENTAR:** nueva clave `[api] extra_allowed_hosts` que se suma al allowlist de `TransportSecuritySettings` del MCP (además de los `bind_hosts` y loopback), cerrando el deferred de MagicDNS.
- **Q3 = AHORA:** verificación real sobre `testing` (192.168.0.39, tailnet `100.77.163.61`) sin romper nada: instalación en rutas dedicadas y, si algo no cuadra, reversión documentada; no se toca ningún servicio preexistente.

## Boundaries & Constraints

**Always:**
- Escucha solo en tailnet + loopback (NFR-1/AD-6): nunca `0.0.0.0` ni la LAN física.
- `ExecStart` con la ruta explícita del intérprete del venv gestionado por `uv` (nunca el Python de sistema `3.11`); `uv sync` reproduce el entorno fijado por `uv.lock`.
- Idempotencia: re-ejecutar el instalador no regenera claves, no pisa una config existente, no duplica la unidad y no rompe el servicio.
- Claves Ed25519 `600` en directorio `700`; config `600`, propiedad de `wakemeup`; DB en `/var/lib/wakemeup/`.
- Logs del servicio a journald (stdout/stderr); nunca passwords, claves ni contenido de `authorized_keys` (FR-11).
- El servicio arranca aunque la tailnet tarde o falte: retry acotado y degradación a loopback con warning; jamás `exit` por una interfaz ausente (evita crash-loop con `Restart=always`).

**Never:**
- No TLS (el canal es la tailnet cifrada). No tocar `uv.lock`/dependencias. No automatizar `sudoers` de máquinas remotas. No desplegar sobre la máquina de desarrollo. No borrar inventario/DB en upgrades.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Bind tailnet viva | `bind_hosts=[100.x,127.0.0.1]`, puerto libre | dos sockets bindeados; API responde en ambos | — |
| Tailnet tardía/ausente | ídem, host tailnet aún no existe | bindea loopback, reintenta hasta el timeout y arranca con warning | nunca aborta |
| Host no local/inválido | un `bind_hosts` no asignable | se omite con warning; el resto se bindea | log warning |
| Instalación 1ª vez | host limpio con `uv` | crea usuario+claves+config+unidad, `uv sync`, `enable --now`, healthcheck 200 | fallo → rc≠0 con mensaje |
| Re-ejecución | ya instalado | no regenera claves, no pisa config, unidad idéntica, servicio intacto | idempotente |
| Healthcheck | servicio arriba | `GET http://127.0.0.1:<port>/api/v1/status` → 200 | si no 200 → reporta y rc≠0 |

</frozen-after-approval>

## Code Map

- `backend/src/wakemeup/server.py` (nuevo) — runner uvicorn: `resolve_bind_hosts`, `bind_sockets` con retry, `main`.
- `backend/src/wakemeup/install.py` (nuevo) — instalador idempotente con un `Runner` de subprocesos inyectable (testeable); detección AUTO de tailnet IP + DNSName.
- `backend/src/wakemeup/config.py` — `ApiSettings`: `bind_retry_seconds` y `extra_allowed_hosts`.
- `backend/src/wakemeup/api/__init__.py` — `_build_transport_security` (hosts extra/MagicDNS).
- `backend/deploy/wakemeup.service` — rutas finales, `After=tailscaled.service`, sin `TODO`.
- `backend/deploy/install.sh` (nuevo) — shim fino que invoca `wakemeup-install` con `uv`.
- `backend/deploy/README.md` (nuevo) — checklist manual de despliegue.
- `backend/config/config.toml.example` — `[api]` con bind tailnet+loopback y notas de deploy.
- `backend/pyproject.toml` — `[project.scripts]` `wakemeup-install`/`wakemeup-server`.
- `backend/tests/test_server.py`, `backend/tests/test_install.py` (nuevos); ampliar `test_config.py`.

No tocar: contratos REST/MCP, `adapters/*`, esquema de DB, app Android.

## Tasks & Acceptance

**Execution:**
- [x] `backend/src/wakemeup/config.py` — añadir `bind_retry_seconds` y `extra_allowed_hosts` a `ApiSettings` — gobernar el bind real y el allowlist del MCP.
- [x] `backend/src/wakemeup/server.py` — runner multi-host con retry/degradación; `main` lee `Settings` y arranca `uvicorn.Server.run(sockets=…)`.
- [x] `backend/src/wakemeup/api/__init__.py` — incluir `extra_allowed_hosts`/MagicDNS en `_build_transport_security`.
- [x] `backend/src/wakemeup/install.py` — instalador idempotente (detección AUTO de tailnet, usuario, claves, config, `uv sync`, unidad, `daemon-reload`, `enable --now`, healthcheck) con `Runner` inyectable.
- [x] `backend/deploy/wakemeup.service` — `ExecStart` del venv de uv, `WorkingDirectory=/var/lib/wakemeup`, `Environment=WAKEMEUP_DB__PATH=…`, `After=tailscaled.service`.
- [x] `backend/deploy/install.sh` + `backend/deploy/README.md` — shim y checklist manual (incluida la reversión sobre `testing`).
- [x] `backend/config/config.toml.example` — `[api]` de despliegue real con `extra_allowed_hosts`.
- [x] `backend/tests/test_server.py` — matriz de bind/retry con sockets reales en loopback.
- [x] `backend/tests/test_install.py` — idempotencia con `Runner` falso y `tmp_path` (claves/config/unidad).
- [x] `backend/tests/test_config.py` — defaults y override de las claves `[api]` nuevas.

**Acceptance Criteria:**
- Given `bind_hosts=[100.x,127.0.0.1]` con la tailnet viva, when arranca el runner, then la API responde en ambos y `GET /api/v1/status` devuelve 200.
- Given un host tailnet ausente, when arranca el runner, then no aborta: bindea loopback, reintenta hasta `bind_retry_seconds` y sigue con warning.
- Given un host limpio con `tailscale` instalado, when se ejecuta el instalador, then escribe `bind_hosts` con la IP tailnet detectada + loopback y `extra_allowed_hosts` con el MagicDNS, crea usuario `wakemeup`, par Ed25519 `600`/`700`, config y unidad, sincroniza con uv y deja el servicio habilitado con healthcheck 200.
- Given una instalación existente, when se re-ejecuta el instalador, then las claves y la config se conservan, la unidad no cambia y el servicio sigue.
- Given la unidad instalada, then `ExecStart` usa el intérprete del venv de uv y no el Python de sistema.
- Given `extra_allowed_hosts=[<dnsname>]`, when se construye la seguridad de transporte del MCP, then el `Host` header del nombre MagicDNS queda permitido además de `bind_hosts`/loopback.
- Given la story completa, then pytest verde y commit + push (requisito transversal).

## Implementation Notes

## Spec Change Log

## Review Triage Log

## Verification

**Commands:**
- `cd backend && timeout 300 uv run pytest -q` — expected: todo verde (incluye `test_server.py`/`test_install.py`).
- `cd backend && timeout 60 uv run python -m wakemeup.server` (con `[api]` de loopback) — expected: "Uvicorn running on" y `curl -fsS http://127.0.0.1:8080/api/v1/status` → 200.

**Manual checks (if no CLI):**
- En `testing` (192.168.0.39): ejecutar `deploy/install.sh`, comprobar `systemctl status wakemeup` activo, `journalctl -u wakemeup` sin credenciales, re-ejecutar y verificar idempotencia, y alcanzar `http://<tailnet>:8080/api/v1/status` desde la tablet. Reversión: `systemctl disable --now wakemeup` + borrar usuario/rutas dedicadas (nada preexistente se toca).
