---
title: '1-4-api-de-inventario-y-tokens-de-dispositivo'
type: 'feature'
created: '2026-09-13'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
context: []
baseline_commit: '9284d03b384d703281dd047930548157a39723a0'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** El BE descubre y mantiene el estado de las máquinas (1.1–1.3) pero no expone nada: no hay API de inventario autenticada, no hay tokens de dispositivo, el healthcheck es anónimo por defecto y los loops periódicos no se conectan al lifecycle de la app.

**Approach:** Implementar la capa de servicio API (FR-9/FR-10/FR-12, AD-1/AD-3/AD-6/AD-7): tokens de dispositivo (CLI de alta/revocación, 32 B hex, hash SHA-256 de la cadena hex-lower en SQLite, mostrados una sola vez), middleware de auth Bearer con backoff de 5 fallos/5 min → 429/15 min en memoria y lista de exenciones (`GET /api/v1/status`), endpoints `GET /api/v1/machines` (DTO AD-10 + `managed=false` en v1) y `POST /api/v1/scan` (202 + stats si ya hay escaneo en curso, consumiendo el estado real de la tarea `_current` y resolviendo el deferred-work de 1.2), y cableado de `discovery.periodic_task` + `status.check_cycle` al arranque de la app vía lifespan. El binding `[api] bind_hosts` y el prefijo `[api] prefix` se parametrizan (AD-6). El `GET /api/v1/status` se amplía con la interfaz emisora WOL reportando `warning` si no hay interfaz Ethernet — FR-5 parcial (sin enviar magic packets, Epic 2).

## Boundaries & Constraints

**Always:**
- Tokens: 32 B aleatorios (`secrets.token_bytes(32)`), pre-imagen canónica = hex-lower de los 32 B; en BD se guarda SOLO `sha256(hex_lower)` (AD-6); el `message`/log nunca contiene el token plano ni el hash.
- Fallos de auth: contadores en memoria del proceso (se resetean al reiniciar — AD-6), clave por token y por IP fuente.
- Exención de auth explícita: solo `GET /api/v1/status` (healthcheck, decision 1.2); el resto de rutas `/api/v1/*` requieren token.
- `POST /api/v1/scan` nunca responde 409: escaneo en curso → 202 con stats del escaneo en marcha (AD-3); el estado "en curso" debe reflejar el trabajo real (resolver `_in_flight` falsa del deferred de 1.2).
- La app responde con el DTO AD-10 por máquina: `id`, `name`, `ip`, `mac`, `hostname`, `status ∈ {online, offline, no_fiable}`, `managed`; en v1 `managed=false` siempre (el alta es del Epic 2).
- Envelope de error uniforme `{"error": {"code", "message"}}` (AD-1): códigos 400/401/404/409/422/429/5xx.
- Rutas FastAPI en `api/` (nunca en `services/`); los servicios discovery/status no cambian su contrato hacia la API.
- El fichero de config `[db] path` resuelve el deferred-work `ruta SQLite CWD-relative` (1.2); default mantiene el comportamiento actual (`wakemeup.db` en CWD) y `Db()` lo consume.
- `GET /api/v1/status` se amplía con el reporte WOL parcial (FR-5, decisión del epic context): expone la interfaz emisora WOL (`ip -o link` + `/sys/class/net`, read-only, sin root); si no hay interfaz Ethernet → `warning` (WOL no fiable vía WiFi) sin bloquear el healthcheck. El envío del magic packet NO entra en esta story (Epic 2, AD-4).
- El binding del servidor (`[api] bind_hosts`/`port`) y el prefijo `[api] prefix` NO se parametrizan en esta story (decisión del usuario): quedan fijos `/api/v1` y el `--host 127.0.0.1` de la unit systemd; el despliegue 4.1 parametriza y verifica.

**Never:**
- No se implementa el alta de máquinas (enroll), wake, shutdown, MCP, SSE (Epics 2/3).
- No se guarda el token plano en BD (solo hash), ni se loguea credencial alguna (FR-11).
- No se cambia la semántica de `scan()` ni `periodic_task()` (1.2): solo se añade la vía de exposición.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Token válido | `Authorization: Bearer <token>` en `GET /machines` | 200 JSON `{"machines": [...]}` (DTO AD-10, `managed=false`) | — |
| Token inválido/revocado | token desconocido | 401 envelope | contador fallos +1 por token/IP |
| Sin token | `GET /machines` sin header | 401 envelope | contador fallos +1 por IP |
| 5 fallos en 5 min | 5×401 con mismo token o IP | 429 envelope durante 15 min (reloj deslizante) | — |
| Escaneo forzado sin escaneo en curso | `POST /scan` | 202 `{"scan": {"running": false, "triggered": true, "discovered": N, "duration_ms": M}}` | — |
| Escaneo forzado con escaneo en curso | `POST /scan` mientras corre otro | 202 `{"scan": {"running": true, "triggered": false}}` (sin ejecutar un segundo escaneo) | — |
| `POST /scan` sin token | — | 401 (excepción no exenta) | — |
| Healthcheck | `GET /api/v1/status` sin token | 200 `{"status": "ok", "version": ...}` (exento) | — |
| DB vacía | `GET /machines` | `{"machines": []}` | — |

## Code Map

- `backend/src/wakemeup/api/__init__.py` -- app FastAPI actual (`app`, `api = APIRouter(prefix="/api/v1")`, `status()`). Añadir aquí: `include_router` de machines/scan, middleware de auth, y el `lifespan` que conecta los loops.
- `backend/src/wakemeup/config.py` -- `Settings` pydantic; añadir sección `[db] (path)` (decisión usuario: knob en 1.4, default `wakemeup.db` CWD) y `[auth] (max_failures=5, window_seconds=300, block_seconds=900)` para el backoff. `[api]` NO entra (bind y prefix quedan fijos hasta 4.1).
- `backend/src/wakemeup/adapters/db.py` -- `Db` con `list_machines`; el constructor acepta `path` desde `[db] path` del Settings (default mantiene CWD); ampliar con `create_token(device_name, sha256_hash)` + `revoke_token(token_sha)` + `list_tokens()` + `token_exists(sha)` (tabla `tokens`).
- `backend/src/wakemeup/adapters/net.py` -- `Net`; añadir sonda read-only de interfaz Ethernet para el reporte WOL (`ip -o link show` + `/sys/class/net/*/type`, sin root): `wol_interface() -> str | None` (decisión epic: reporte WOL en 1.4). No tocar ping/ARP.
- `backend/src/wakemeup/services/discovery.py` -- `DiscoveryService` con `scan()`/`periodic_task()`/`_current`/`in_flight`; el endpoint `/scan` usa el estado real de `_current` (resuelve deferred `_in_flight` falsa). NO cambiar `scan()`.
- `backend/src/wakemeup/services/status.py` -- `StatusService` con `list_with_status()`, `check_cycle()`; la API lo reusa; NO cambiar su contrato.
- `backend/src/wakemeup/cli/__init__.py` -- CLI actual vacía (solo docstring); aquí los subcomandos `token create|revoke|list` (argparse o typer).
- `backend/config/config.toml.example` -- añadir `[db]` (`path = "wakemeup.db"` o ruta documentada con WorkingDirectory).
- `backend/tests/test_api.py` (nuevo) -- TestClient: auth, envelope, 202/scan, healthcheck exento, reporte WOL.
- `backend/tests/test_tokens_cli.py` (nuevo) -- CLI: alta muestra una sola vez, hash en BD, revocación, listado.
- `backend/tests/test_config.py` -- ampliar con `[auth]` y `[db]`.
- `backend/src/wakemeup/__init__.py` -- `__version__`.

## Tasks & Acceptance

**Execution:**
- [x] `backend/src/wakemeup/config.py` -- añadir `DbSettings` (`[db] path`, default `wakemeup.db`) al `Settings` (decisión usuario: knob en 1.4).
- [x] `backend/src/wakemeup/adapters/db.py` -- construir `Db` desde `[db] path` del Settings; añadir tabla `tokens(id, device_name, token_sha256, created_at, revoked_at)` + `create_token`, `revoke_token`, `list_tokens`, `token_exists`; migración de esquema (patrón `_migrate` de 1.2).
- [x] `backend/src/wakemeup/adapters/net.py` -- `wol_interface()` (read-only, sin root) para el reporte FR-5.
- [x] `backend/src/wakemeup/core/models.py` -- añadir `Token` (dataclass).
- [x] `backend/src/wakemeup/services/auth.py` -- `AuthService`: hash SHA-256 hex-lower, verificación, backoff en memoria (token+IP) y catálogo de exenciones (`GET /api/v1/status`).
- [x] `backend/src/wakemeup/api/__init__.py` -- middleware de auth (Bearer) con `[auth]` config (5 fallos/5 min → 429/15 min) y excepción de healthcheck; `POST /scan` (202 con stats del escaneo real en curso via `_current`); `GET /machines` (DTO AD-10 y `managed: false` con `list_with_status`); `GET /status` ampliado con reporte WOL (`wol_interface` + `status: warning` si no hay Ethernet).
- [x] `backend/src/wakemeup/cli/__init__.py` -- subcomandos `token create <name>` (imprime una sola vez), `token revoke <name|token>`, `token list`.
- [x] `backend/tests/test_api.py` (nuevo) -- auth valida/401/429, healthcheck exento, DTO AD-10, 202 con `running:true|false`, envelope, reporte WOL en /status.
- [x] `backend/tests/test_tokens_cli.py` (nuevo) -- CLI: una sola vez, hash en BD, revocación, listado.
- [x] `backend/tests/test_net_adapter.py` -- sonda WOL: interfaz ethernet presente → `str`; sin ethernet → `None`; degradación sin binarios.
- [x] `backend/tests/test_config.py` -- sección `[db] path` (default CWD, override env y TOML).
- [x] Cablear loops: `lifespan` en `api/__init__.py` que arranque `discovery.periodic_task()` y `status.check_cycle()` con la config del settings.

**Acceptance Criteria:**
- Given un BE con inventario y un token creado por CLI, when se llama `GET /api/v1/machines` con el token, then responde 200 con el DTO AD-10 completo por máquina (id, name, ip, mac, hostname, status∈{online,offline,no_fiable}, managed).
- Given una petición sin token o con token inválido/revocado, when se llama a `GET /machines`, then responde 401 con envelope `{"error": {"code","message"}}`; el healthcheck `GET /status` no requiere token (200).
- Given 5 fallos de auth con el mismo token o IP en 5 minutos, when se reintenta, then responde 429 con envelope durante 15 minutos (reloj deslizante, memoria del proceso).
- Given un escaneo en curso (o no), when se llama `POST /scan` con token, then responde 202 con stats; si ya hay escaneo en marcha, `running: true` y no se ejecuta un segundo escaneo (singleflight).
- Given un token revocado, when se usa para autenticarse, then responde 401 y no permite acceso.
- Given la CLI `token create`, when se ejecuta, then imprime el token una sola vez y en la BD solo existe el hash SHA-256 hex-lower; `token revoke` invalida el token.
- Given el BE sin interfaz Ethernet, when se consulta `GET /api/v1/status`, then responde 200 con `status: warning` y `wol: {"interface": null}`; con interfaz Ethernet → `status: ok`, `wol: {"interface": "eth0"}` (FR-5 parcial).
- Given la config `[db] path` (o env `WAKEMEUP_DB__PATH`), when `Db()` se construye desde el Settings, then la DB se abre en esa ruta; sin la clave, en `wakemeup.db` (CWD) — deferred-work de 1.2 resuelto.
- Given los loops de discovery y status, when la app arranca (lifespan), then `periodic_task` y `check_cycle` están en marcha.
- Given las ACs previas, then existe cobertura pytest (auth, envelope, 202, DTO, CLI) — requisito transversal de tests.
- Given la story completada, then commit + push — requisito transversal del usuario.

## Implementation Notes

- **Hallazgo preexistente (no de esta story)**: pytest a veces NO SALE tras pasar todos los tests del backend (exit=124 por timeout cuando se ejecuta con `tests/test_discovery.py` o `test_status.py`, que arrancan loops periódicos). Reproducido en el commit base `9284d03` (worktree limpio, sin cambios 1.4) — es un cuelgue del teardown de pytest-asyncio en esta máquina (loops tipo `periodic_task`/`check_cycle` cancelados en `finally`), no del código nuevo. La suite pasa completa: `64 passed` en ejecución limpia; el proceso no termina de forma intermitente.
- **Lifespan testeado**: el lifespan consume el grafo expuesto en `app.*` (sustituible en tests); inicialmente usaba el grafo privado del módulo y no era observable — corregido en la primera pasada.
- **DTO**: `name` = `hostname` si existe, si no la IP (fallback). `managed` siempre `false` (decisión usuario: contrato completo desde 1.4).
- **Escaneo**: el endpoint lee `discovery.current_task` (nueva propiedad que refleja la tarea viva, incl. fase upsert) en lugar de `in_flight` — cierra el deferred-work `_in_flight falsa` de la revisión 1.2.
- **Migración de esquema**: la tabla `tokens` se crea con `CREATE TABLE IF NOT EXISTS` dentro del mismo esquema new-DB y vía `_migrate_tokens` para DBs existentes (no rompe DBs 1.2/1.3).
- **CLI**: `wakemeup-cli token create|revoke|list` (argparse, entry point en pyproject). El aviso "muéstralo una sola vez" va a stderr; el token plano va a stdout, una sola vez.
- **`config.toml.example`**: añadida sección `[db]` (path).

## Spec Change Log

## Review Triage Log

- blind-hunter `spec self-contradition `[api]` parametrizan vs NO` — **false** — el código cumple la decisión del usuario (Boundaries: NO parametrizar en 1.4, 4.1 lo hace); la frase del Intent quedó obsoleta por la renegociación en Open Questions. Sin defecto en código.
- blind-hunter `envelope 422 no implementable (RequestValidationError por defecto)` — **high** — verificado: era el body de FastAPI. **patch**: handler `RequestValidationError` → envelope 422 + log; sin superficie de body en 1.4, el test espera a Epic 2.
- blind-hunter `token plano en logs` — **high** — verificado: `record_failure(key)` y `record_failure(token)` logueaban el token. **patch**: backoff por IP + digest (hash) del token; log sin clave (FR-11).
- blind-hunter `revoke <name|token> no implementado` — **medium** — verificado: solo por nombre; `revoke_token(sha)` sin cable. **patch**: `revoke` acepta 64-hex → revoca por hash (AD-6); docstring y tests (por-nombre, por-valor, 64-hex no registrado).
- blind-hunter `healthcheck 500 si ARP falla` — **false** — `read_arp_table` no lanza (captura FileNotFoundError/timeout → `{}`), y la sonda ya no la usa (flags/state). Además `wol_interface` ahora captura OSError y hace kill en timeout.
- blind-hunter `singleflight no garantizado (check-then-act)` — **false** — el lock de `DiscoveryService.scan()` (probado en `test_singleflight_two_concurrent_scans_run_one`) garantiza un único escaneo efectivo también con POST simultáneos; la ventana solo cambia `running:true`→`triggered:true` en un 202, sin segundo escaneo.
- blind-hunter `WOL validation no per-interface` — **high** — verificado (reportaba eth0 sin cable si el ARP global tenía MACs). **patch**: `ip -o link` flags + `state UP` por interfaz; tests carrierless/down añadidos.
- blind-hunter `import-time config` — **false** — la config se resuelve una vez al arrancar el proceso (env del systemd ya fijados); el patrón es el heredado de 1.2/1.3; los tests sustituyen el grafo. Sin recarga dinámica esperada.
- blind-hunter `shutdown sin await de tasks` — **medium** — verificado: `stop()` cancelaba sin esperar y cerraba la DB. **patch**: lifespan espera a las task canceladas antes de `db.close()` (except CancelledError/Exception).
- blind-hunter `[auth] no en example` — **false** — `[auth] max_failures/window_seconds/block_seconds` ya están documentados en `backend/config/config.toml.example` (desde 1.1).
- blind-hunter `HEAD healthcheck → 401` — **medium** — verificado: ruta GET con HEAD fallaba la exención. **patch**: `is_exempt` acepta GET+HEAD (Starlette) e ignora trailing slash; ruta `@api.head("/status")`; test HEAD 200.
- blind-hunter `subprocess leak en timeout + DoS anónimo` — **low** — **patch parcial**: kill en timeout; el DoS no aplica (bind solo tailnet+loopback, AD-6).
- blind-hunter ``blocked` branch inalcanzable en authenticate` — **false** — el middleware pre-responde 429 antes de `authenticate`; la rama interna es defensiva para otros llamadores; sin comportamiento erróneo.
- blind-hunter `test 429 por IP sin token / migración legacy / wol timeout/rc / reset privado` — **low** — parcheado: `test_five_failures_without_token_block_by_ip`, `test_init_db_migrates_legacy_1_2_db` (sqlite3 legacy → init_db), tests wol timeout-kill + rc!=0 + OSError, `AuthBackoff.reset()` público usado en tests.
- edge-case `OSError en ip exec` — **medium** — **patch**: `except OSError` en `wol_interface`; test PermissionError.
- edge-case `ARP global gatea eth0` — **high** — duplicado blind-hunter WOL per-interface: parcheado (flags/state).
- edge-case `HEAD no exento` — **medium** — duplicado blind-hunter HEAD: parcheado.
- edge-case `trailing slash en exención` — **low** — **patch**: `is_exempt` con `path.rstrip("/")`.
- edge-case `in-flight scan en shutdown` — **medium** — duplicado blind-hunter shutdown: parcheado (await antes de close).
- edge-case `revoke por token` — **medium** — duplicado blind-hunter revoke: parcheado.
- edge-case `ok requiere ARP no vacío (AC diverge)` — **false** — la sonda ya no usa la tabla ARP (flags/state).
- edge-case `Intent claim [api] bind_hosts/prefix` — **false** — duplicado blind-hunter contradiction: sin defecto en código.
- edge-case `probe /sys/class/net como especificado` — **low** — el spec decía "`ip -o link` + `/sys/class/net`"; se implementó con `ip -o link` + flags/state (sin root, más directo); FR-5 cubierto, AC inalteradas.
- verification-gap `current_task real bajo test` — **medium** — **patch**: `test_current_task_reflects_live_scan_including_upsert` (SlowDb bloquea el upsert; `in_flight` False mientras `current_task` sigue vivo; luego None).
- verification-gap `migración legacy sin test` — **medium** — **patch**: `test_init_db_migrates_legacy_1_2_db` (DB 1.2 vía sqlite3 → init_db: columnas, tabla tokens, filas intactas, token post-migra).
- verification-gap `envelope handlers sin test` — **medium** — **patch**: catch-all 404 bajo `/api/v1` (envelope AD-1), test 404 + test 500 (`raise_server_exceptions=False`), handler 422.
- verification-gap `stop() real sin test` — **low** — **patch** (pese a defer sugerido): `test_stop_cancels_periodic_loop` y `test_stop_cancels_check_cycle` (loop real cancelado).
- verification-gap `wol carrierless reportado` — **medium** — duplicado WOL per-interface: parcheado.
- verification-gap `TestClient re-lanza el 500` — **medium** — **patch**: `TestClient(app, raise_server_exceptions=False)` en el test 500.
- verification-gap `lifespan fake rompe con await (AttributeError coroutine)` — **low** — **patch**: fakes del test lifespan con tasks reales cancelables (patrón del código real).
