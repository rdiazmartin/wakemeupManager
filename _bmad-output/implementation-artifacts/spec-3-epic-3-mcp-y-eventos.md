---
title: 'Epic 3: Agente IA en la red — MCP y eventos en vivo (stories 3.1–3.5)'
type: 'feature'
created: '2026-09-15'
status: 'done'
baseline_commit: 'd6f6ad8'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-3-context.md'
  - '{project-root}/_bmad-output/planning-artifacts/architecture/architecture-wakemeupManager-2026-09-13/ARCHITECTURE-SPINE.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** El BE no expone un servidor MCP (un agente IA no puede consultar ni controlar las máquinas) ni eventos push: la app solo refresca por polling cada 30 s, y ninguna acción del agente se notifica al usuario. La infraestructura de tokens solo tiene tokens de dispositivo (sin discriminador), y no hay bus de eventos ni stream SSE.

**Approach:** Epic 3 completo en un solo encargo (decisión del usuario): servidor MCP con SDK oficial `mcp` 2.2.0 y 5 tools que reutilizan los servicios existentes (3.1), token MCP dedicado + aislamiento solo tailnet+loopback (3.2), bus de eventos asyncio + stream SSE en `/api/v1/events` con registro y origen (3.3), suscripción SSE en la app con fallback de polling y reconexión con backoff (3.4), y notificación del sistema solo para eventos de origen `mcp` (3.5). Cada story conserva sus tests (pytest / Robolectric) y commit + push al finalizarla; el commit final del epic integra el sprint-status y cierra `epic-3`.

## Boundaries & Constraints

**Always:**
- Contrato wire existente intacto: envelope de error `{"error":{code,message}}` (AD-1), prefijo `/api/v1`, `GET /api/v1/status` exento de auth, backoff 5/5 min → 429/15 min (AD-6). El catch-all 404 debe seguir registrado el último.
- SDK MCP oficial `mcp==2.2.0` (`from mcp.server import MCPServer`, `@server.tool()`, `server.streamable_http_app()` montado como sub-app). Los tools llaman a los servicios existentes: NUNCA implementación propia paralela (AD-12).
- Tools de v1 exactamente: `list_machines`, `get_machine_status`, `wake_machine`, `shutdown_machine`, `force_scan`. Sin tool de alta ni de gestión de claves; el agente nunca ve passwords.
- Token MCP independiente de los de dispositivo: mismo formato 32 B hex mostrado una sola vez, almacenado solo como `SHA256(hex-lower)`, revocable por CLI (FR-10b). Un token de dispositivo no abre el MCP y un token MCP no abre REST/SSE; revocar uno no afecta al otro.
- El MCP se alcanza solo desde loopback + tailnet (`100.64.0.0/10`, `fd7a:115c:a1e0::/48`), nunca desde la LAN física ni fuera de la VPN (NFR-1, AD-12), con `TransportSecuritySettings` para el host header. Los fallos de auth del MCP se registran (FR-11).
- Bus de eventos (AD-11): todo cambio de estado y toda acción de control de API/MCP/escaneo (a) se persiste en el registro de actividad (FR-11) y (b) se emite como evento SSE JSON `{type, machine, timestamp, origin ∈ {api,mcp,scan,periodic}}` a los suscriptores. Sin suscriptores → el evento se omite (v1 sin cola). Nunca transporta credenciales ni claves.
- Origen por actor: acciones de la app → `api`; acciones del agente, incluido `force_scan` → `mcp`; escaneo forzado por API → `scan`; escaneo periódico y transiciones del loop de estado → `periodic`.
- Stream SSE en `/api/v1/events`, Bearer de token de dispositivo, solo tailnet (AD-6/AD-11). Con stream activo el estado se refleja en ≤2 s; caído o con ahorro de batería, el polling 30 s cubre el hueco (FR-12/FR-18).
- App: SSE con Ktor (`ktor-client-core` 3.5.2 ya incluye el plugin), reconexión con backoff exponencial silenciosa, pausa en ahorro de batería dejando el polling activo; DTO de evento deserializado robustamente (evento mal formado se ignora sin crash) (AD-10/AD-11).
- Notificación del sistema SOLO para eventos de origen `mcp` (bandeja, con la app abierta o en segundo plano, channel propio); el resto de orígenes solo actualizan la fila, sin snackbar/spinner (FR-18, UX-DR7). Requiere `POST_NOTIFICATIONS`; denegado → sin notificación, la fila se refleja igual y la app sugiere activarlo en ajustes.
- **Decisión (notificación con stream caído):** la notificación de origen `mcp` funciona también por polling. `GET /machines` amplía el DTO por máquina (aditivo, sin romper AD-10) con `last_origin` y `last_change_at`; el BE persiste el origen del último cambio de estado (columna `machines.last_origin`). La app, en cada refresh de polling, detecta cambios nuevos con `last_origin == "mcp"` (timestamp posterior al último visto/notificado) y notifica en ≤30 s — cubre el "fallo" de Flow 4. Se evita duplicar la notificación si el mismo cambio ya llegó por SSE.
- **Decisión (momento del permiso):** `POST_NOTIFICATIONS` se pide al primer evento de origen `mcp` recibido (SSE o polling), con explicación; si se deniega, la fila se actualiza igual y la app sugiere activarlo en ajustes.
- El registro de actividad distingue canal `api|mcp` (FR-11); los tokens se guardan solo como hash (AD-6), la clave privada SSH sigue con 600 y fuera de la DB (AD-7).
- Tests automatizados de los AC de cada story (pytest en el BE; Robolectric/JVM en la app) y commit + push al finalizar cada story (requisito transversal).

**Never:**
- Sin tool de alta ni de gestión de claves; sin passwords SSH en el agente (FR-16, AD-12).
- Sin implementación MCP propia ni lógica de control dentro de `api/mcp*`: se delega en `services/` (AD-4/AD-12).
- Sin notificaciones para orígenes `api`/`scan`/`periodic`; sin indicador visual de "conexión en vivo" (UX lo banea).
- Sin cola de entrega de eventos ni replay para suscriptores ausentes.
- No romper el DTO de máquina (`status`, `managed`) ni los endpoints/tokens existentes; el tipo MCP es aditivo.
- No cambiar el mecanismo de auth de dispositivo ni la superficie de `POST /scan` (202).
- Fuera de alcance: instalador/bind real de uvicorn (4.1), onboarding/errores (4.2) y accesibilidad (4.3) más allá de lo que 3.5 necesita para el permiso de notificaciones.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| MCP: descubrimiento | cliente MCP autenticado | 5 tools listadas (`list_machines`, `get_machine_status`, `wake_machine`, `shutdown_machine`, `force_scan`); ninguna de alta/claves | N/A |
| MCP: sin token / token inválido | conexión MCP | sesión rechazada (401 / error de protocolo) | registro del fallo de auth |
| MCP: token de dispositivo | conexión MCP con token `api` | rechazada (no vale como token MCP) | 401 / error de protocolo |
| MCP: desde LAN física | cliente no loopback/tailnet | rechazada | 403/421 del guard + registro |
| MCP: token MCP revocado | token MCP revocado | sesión rechazada; los tokens de dispositivo siguen válidos | 401 |
| Tool wake vía MCP | `wake_machine` de máquina gestionada | mismo `WakeService`; evento origen `mcp`; log canal `mcp`; notificación en la app | 409/422/404 como la API |
| Tool shutdown vía MCP | fingerprint coincide / no coincide | ejecuta / no ejecuta y marca `no_fiable`; evento origen `mcp`; log canal `mcp` | 409/502/503 |
| Tool force_scan vía MCP | `force_scan` | 202 con stats; transiciones de estado con origen `mcp` | nunca 409 |
| Bus: cambio de estado | online/offline/no_fiable desde API/MCP/scan/periodic | persiste y emite `{type, machine, timestamp, origin}` | N/A |
| Bus: sin suscriptores | evento sin clientes SSE | se omite (no se encola) | N/A |
| SSE: conexión | `GET /api/v1/events` con token de dispositivo | stream `text/event-stream`; recibe los eventos emitidos | 401 sin token / token MCP |
| SSE: suscriptor cae | cliente desconecta a mitad | el servicio sigue; no rompe la emisión | log, sin excepción propagada |
| App: evento de otro origen | evento `api`/`scan`/`periodic` | fila actualizada sin spinner ni snackbar | N/A |
| App: evento origen `mcp` | evento `mcp` con permiso concedido | notificación del sistema (`«máquina» — El agente IA <acción>`) + fila actualizada | N/A |
| App: permiso denegado | evento `mcp` sin `POST_NOTIFICATIONS` | fila actualizada; sin notificación; sugerencia en ajustes | N/A |
| App: evento mal formado | JSON inválido en el stream | se ignora sin crash; el stream continúa | log |
| App: stream cae | corte de red | reconexión con backoff exponencial silenciosa; polling sigue cubriendo | N/A |
| App: ahorro de batería | batería baja/ahorro | stream pausado; polling activo | N/A |
| App: `mcp` con stream caído | polling ve `last_origin == "mcp"` nuevo | notificación del sistema en ≤30 s + fila actualizada | N/A |
| App: duplicado SSE+polling | el mismo cambio ya notificado por SSE | no se notifica dos veces | N/A |

</frozen-after-approval>

## Code Map

- `backend/pyproject.toml` — añadir dependencia `mcp==2.2.0` (arrastra `sse-starlette`, `httpx2`); `wakemeup-cli = wakemeup.cli:main` ya existe.
- `backend/src/wakemeup/adapters/db.py` — `_SCHEMA`/`_COLUMN_MIGRATIONS`: añadir `tokens.kind` (TEXT default `'device'`) y `machines.last_origin` (TEXT, nullable); `create_token(name, hash, kind='device')`, `token_exists(digest, kind)`, `list_tokens` con `kind`; `set_status`/`set_state` guardan también el origen del cambio; reutilizar `record_activity`/`list_activity` para el bus (ya tiene `channel`). No romper firmas existentes (valores por defecto).
- `backend/src/wakemeup/core/models.py` — `Token` añade `kind`; `Machine` añade `last_origin`; NUEVO `Event` (type, machine_id/ip, timestamp, origin) si procede. `MachineDTO` añade `last_origin`/`last_change_at` (aditivo, AD-10 intacto en el resto de campos).
- `backend/src/wakemeup/services/events.py` (NUEVO) — bus asyncio pub/sub (`subscribe()` async context manager/cola, `publish(event)`): sin suscriptores → descarta; nunca bloquea a los publicadores; persiste vía `ActivityService` cuando corresponda.
- `backend/src/wakemeup/services/activity.py` — `CHANNEL_API`/`CHANNEL_MCP` ya definidos; reutilizar `record(...)`. Posible helper de publicación o el bus lo inyecta quien actúa.
- `backend/src/wakemeup/services/wake.py` `shutdown.py` `enrollment.py` — publicar evento con el origen recibido (`channel`) tras la acción; `WakeService.wake`/`ShutdownService.shutdown` ya aceptan `channel`/`token` (mapear `channel api|mcp` → `origin`). No cambiar contratos.
- `backend/src/wakemeup/services/discovery.py` — `_run()`/`scan()`: emitir `scan_done` con origen (`scan` forzado o `mcp` si lo pidió el agente; `periodic` en `periodic_task`); `force_scan` MCP debe propagar `origin='mcp'` a las transiciones de estado resultantes.
- `backend/src/wakemeup/services/status.py` — `check_all()` (L49–52): comparar estado previo vs nuevo antes de `set_status` y emitir transición `online/offline/no_fiable` con origen `periodic`; no publicar si no cambia. Respetar el guard existente de `no_fiable`.
- `backend/src/wakemeup/mcp/` (NUEVO) o `backend/src/wakemeup/api/mcp.py` — construir `MCPServer`, registrar las 5 tools envolviendo los servicios (async), `streamable_http_app(transport_security=TransportSecuritySettings(...))`; montarlo en `app` bajo `/api/v1/mcp` ANTES de `include_router(api)` (para vencer al catch-all).
- `backend/src/wakemeup/api/__init__.py` — `GET /machines` incluye `last_origin`/`last_change_at` en el DTO; nuevo `GET /api/v1/events` (`StreamingResponse` `text/event-stream` con generador async suscrito al bus) antes del catch-all; middleware de auth MCP (valida token MCP y guard de IP loopback/tailnet) eximiendo `/api/v1/mcp` del middleware de dispositivo; empezar/parar el bus y la sesión MCP en `lifespan` (`mcp.server.session_manager.run()` — el lifespan del submount no corre); inyectar `app.events`.
- `backend/src/wakemeup/services/auth.py` — reutilizar `sha256_hex_lower`; añadir verificación por `kind` (o un `McpAuthService`) y reutilizar `AuthBackoff`. Exención de dispositivo para `/api/v1/mcp`.
- `backend/src/wakemeup/config.py` + `backend/config/config.toml.example` — sección `[api]` (bind_hosts/port/prefix ya en el ejemplo, hoy sin parsear) mínima para el guard; mantener compatibilidad de config.
- `backend/src/wakemeup/cli/__init__.py` — `token create` con `--kind {device,mcp}` (default `device`) y `token list` mostrando `kind`; sin romper `token create/revoke/list` existentes.
- `backend/tests/` — NUEVOS `test_events.py` (bus + SSE), `test_mcp.py` (tools, sin tools de alta, auth/guard, canal mcp); ampliar `test_api.py` (Fake bus + `app.events`), `test_tokens_cli.py` (kind mcp), `test_status.py`/`test_discovery.py` (emisión de transiciones/scan_done). No hay `conftest.py`: fixtures locales por fichero.
- `android/app/build.gradle.kts` — sin nuevas deps (SSE en `ktor-client-core`); confirmar `ktor-client-okhttp` soporta SSE.
- `android/app/src/main/java/com/wakemeup/manager/data/remote/WakemeupApi.kt` — método(s) SSE (`serverSentEvents` con Bearer de `settings.deviceToken`) que emitan un `Flow<MachineEvent>`; manejo de reconexión/backoff y DTO robusto.
- `android/.../data/remote/MachinesDto.kt` — NUEVO DTO de evento (`type`, `machine`/`id`, `timestamp`, `origin`) con `ignoreUnknownKeys`; `MachineDto` añade `lastOrigin`/`lastChangeAt` (opcionales) para el fallback.
- `android/.../domain/` — modelo de evento + mapping de origen/acción a texto de notificación; `Machine` añade `lastOrigin`/`lastChangeAt`.
- `android/.../ui/machines/MachineListViewModel.kt` — `applyEvent(event)`: actualizar la máquina afectada en `_uiState` sin spinner/snackbar; arrancar/parar la suscripción SSE y pausarla con `setBatterySaver(enabled)` (L209–216) dejando el polling; reconexión con backoff; en el polling, detectar `lastOrigin == "mcp"` nuevo y notificar sin duplicar lo ya visto por SSE. Mantener `refresh(silent)`/`_sessionInvalid` intactos.
- `android/.../data/local/` — permiso/estado de notificaciones; reutilizar `SettingsRepository`/`SecretStore` (no passwords). `MainActivity.kt` composition root: construir el componente SSE y el notificador (sin Application class; hooks `settingsFactory`/`httpClientFactory`).
- `android/.../notifications/` (NUEVO) — `NotificationChannel` + emisión de la notificación para origen `mcp` (título=máquina, cuerpo=acción), pedido/sugerencia de `POST_NOTIFICATIONS` al primer evento `mcp`; disparado al recibir el evento (SSE o polling), no en la UI.
- `android/app/src/main/AndroidManifest.xml` — añadir `android.permission.POST_NOTIFICATIONS`.
- `android/app/src/main/res/values/strings.xml` + `values-en/strings.xml` — strings ES/EN de notificación (canal, título/cuerpo, racional, sugerencia en ajustes).
- `android/app/src/test/` — Robolectric: evento `mcp` → notificación emitida; `api`/`scan`/`periodic` → sin notificación; evento mal formado ignorado; fallback de polling; reconexión con backoff; permiso denegado → solo fila (patrón MockEngine + `StandardTestDispatcher`, `@Config(sdk=[34])`).

## Tasks & Acceptance

**Execution:**
- [x] `backend/src/wakemeup/mcp/` (NUEVO) -- construir `MCPServer` con las 5 tools que envuelven los servicios; montar `streamable_http_app()` en `app` bajo `/api/v1/mcp` -- AC 3.1 (AD-12)
- [x] `backend/src/wakemeup/adapters/db.py` `core/models.py` `cli/__init__.py` -- `tokens.kind` + migración; `create_token(kind)`/`token_exists(digest, kind)`/`list_tokens`; CLI `token create --kind` -- AC 3.2 (FR-10b)
- [x] `backend/src/wakemeup/api/__init__.py` `services/auth.py` `config.py` `config.toml.example` -- auth MCP + guard loopback/tailnet + `TransportSecuritySettings`; exención de `/api/v1/mcp` del middleware de dispositivo; registro de fallos -- AC 3.2 (AD-6/AD-12)
- [x] `backend/src/wakemeup/services/events.py` (NUEVO) `services/{wake,shutdown,enrollment,discovery,status}.py` -- bus pub/sub + emisión en cada cambio/acción con origen correcto; persistencia FR-11 -- AC 3.3 (AD-11)
- [x] `backend/src/wakemeup/api/__init__.py` -- `GET /api/v1/events` SSE (Bearer dispositivo, generador async, sin suscriptores → omite) antes del catch-all; arranque/parada del bus y `session_manager` en lifespan -- AC 3.3 (AD-11)
- [x] `backend/tests/test_events.py` `test_mcp.py` (NUEVOS) + ampliar `test_api.py` `test_tokens_cli.py` `test_status.py` `test_discovery.py` -- bus/SSE, tools/descubrimiento, sin tools de alta, auth/guard/kind, transiciones y scan_done -- AC 3.1–3.3
- [x] `android/.../data/remote/WakemeupApi.kt` `MachinesDto.kt` `domain/` -- cliente SSE (`serverSentEvents`) → `Flow<MachineEvent>`, DTO tolerante -- AC 3.4
- [x] `android/.../ui/machines/MachineListViewModel.kt` -- `applyEvent`, arranque/pausa de la suscripción, backoff, fila sin spinner/snackbar -- AC 3.4 (UX-DR7)
- [x] `android/.../notifications/` (NUEVO) `MainActivity.kt` `AndroidManifest.xml` `strings.xml` ES/EN -- notificación origen `mcp` (SSE y polling), channel, permiso con explicación al primer evento/fallback -- AC 3.5 (FR-18)
- [x] `android/app/src/test/` -- Robolectric: notificación `mcp`, no-notificación otros orígenes, evento mal formado, fallback polling (notifica `mcp` por `lastOrigin`), sin duplicar SSE+polling, backoff, permiso denegado -- AC 3.4/3.5
- [ ] `_bmad-output/implementation-artifacts/sprint-status.yaml` -- marcar 3.x a done y epic-3 done al cierre -- proceso BMAD

**Acceptance Criteria:**
- Given el BE con `mcp==2.2.0` montado en `/api/v1/mcp`, when un agente autenticado descubre tools, then ve exactamente `list_machines`, `get_machine_status`, `wake_machine`, `shutdown_machine`, `force_scan` y ninguna de alta/claves; las de control reutilizan los servicios (MAC válida, fingerprint, comando restringido) (FR-16; AC 3.1)
- Given una tool de control invocada, then emite evento al bus con origen `mcp` y registra canal `mcp` (FR-11/AD-11/AD-12; AC 3.1)
- Given conexión MCP sin token o con token inválido/de dispositivo, then se rechaza la sesión; con token MCP válido desde loopback/tailnet se acepta; desde LAN física/fuera de la VPN se rechaza; revocar el token MCP no afecta a los tokens de dispositivo (FR-10b/FR-17; AC 3.2)
- Given un cambio de estado desde API/MCP/scan/periodic, then se persiste en FR-11 y se emite SSE JSON `{type, machine, timestamp, origin}` a los suscriptores; sin suscriptores se omite; el stream vive en `/api/v1/events` con Bearer de dispositivo y nunca lleva credenciales (FR-11/FR-18/AD-11; AC 3.3)
- Given la app con lista y token válido, when llega un evento, then la fila afectada se actualiza sin spinner ni snackbar (acciones ajenas); con stream activo ≤2 s, caído o en ahorro de batería el polling 30 s cubre; reconexión con backoff silenciosa; evento mal formado ignorado sin crash (FR-18/FR-12/UX-DR7; AC 3.4)
- Given la app con permiso de notificaciones y stream activo, when llega un evento de origen `mcp`, then se muestra notificación del sistema (título=máquina, cuerpo=acción) además de actualizar la fila; con el stream caído, el polling detecta un cambio `last_origin == "mcp"` nuevo y notifica en ≤30 s (sin duplicar lo ya visto por SSE); orígenes `api`/`scan`/`periodic` no notifican; permiso denegado → sin notificación, fila actualizada y sugerencia en ajustes (FR-18; AC 3.5)
- Given los tests, then pytest (BE) y Robolectric (app) verdes para los casos de la matriz; commit + push por story (AC transversal)

## Implementation Notes

- Implementado por subagentes (BE y app) + review de 3 capas (blind-hunter, edge-case, verification-gap) con fixes.
- `mcp==2.2.0`: API real verificada en runtime (`from mcp.server import MCPServer`, `@server.tool()`, `streamable_http_app(*, transport_security=...)`); `FastMCP` no existe en 2.x. Se añadieron `sse-starlette`/`httpx2` como dependencias transitivas.
- El endpoint SSE usa un generador async suscrito al bus con latido cada 1 s; los tests de SSE usan un harness ASGI propio (TestClient/httpx bufferizan la respuesta y no permiten leer el stream incrementalmente).
- Verificación final: **218 pytest** (BE) y **83 Robolectric/JVM** (app), `assembleDebug` OK.

## Spec Change Log

## Review Triage Log

- (blind-hunter) `/api/v1/events` sin guard tailnet (AD-11 exige solo-tailnet) → **high**. → **patch** (guard `is_trusted_client` → 403 + test).
- (edge-case) `force_scan` MCP no propaga `mcp` a las transiciones de estado reales (las emite el loop como `periodic`) → **high** (rompe la notificación del agente por scan). → **patch** (ventana de preservación del origen `mcp` reciente en `StatusService.check_all`).
- (edge-case) el barrido periódico pisa `last_origin='mcp'` al detectar la transición → **high** (el polling nunca notificaría; rompe la decisión del usuario). → **patch** (misma ventana + test con `Db` real).
- (edge-case) poll-primero-luego-SSE duplica la notificación → **medium**. → **patch** (`applyEvent` consulta `seenMcpChanges` antes de notificar + test).
- (blind-hunter/edge-case) `scan_done` con origen `mcp` notifica con título `-` → **medium**. → **patch** (solo notifican acciones de máquina reales y con fila presente + test).
- (edge-case) cold start re-notifica cambios `mcp` antiguos del BE en cada arranque → **medium**. → **patch** (siembra del baseline en el primer poll sin notificar + test).
- (blind-hunter) `HttpTimeout` de 15 s tumba el SSE (bucle de reconexión) → **high**. → **patch** (timeout infinito por petición en la llamada SSE + test).
- (blind-hunter/edge-case) prompt de permiso perdido (`replay=0`) con `markRequested()` ya marcado; y toast de ajustes en bucle → **medium**. → **patch** (`replay=1` + SETTINGS una sola vez + tests).
- (blind-hunter) `is_trusted_client` rechaza loopback IPv6 `::1`/`::ffff:127.0.0.1` → **medium** (MCP inalcanzable por loopback dual-stack). → **patch** (`::1/128` + normalización IPv4-mapped + tests).
- (edge-case) `allowed_hosts` mal formado con `bind_hosts` IPv6 → **medium**. → **patch** (bracket IPv6 + test).
- (blind-hunter) `force_scan` MCP sin `duration_ms` (contrato distinto de `POST /scan`) → **low**. → **patch**.
- (verification-gap) `set_no_fiable(origin)` no aserta persistencia de `last_origin`/`last_change_at` → **patch** (test con `Db` real).
- (verification-gap) rama 429/backoff del MCP sin test → **patch** (test espejo del middleware de dispositivo).
- (verification-gap) `TransportSecuritySettings` de producción sin ejercitar → **patch** (test del allowlist por defecto e IPv6).
- (verification-gap) 401 del SSE → `sessionInvalid` sin test → **patch** (doble de `EventStream` que lanza `ApiException`).
- (verification-gap) prompt de permiso sin test a nivel `MainActivity` → **patch** (fake notifier vía `notifierFactory` + test del diálogo/toast).
- (verification-gap/edge-case) `scan_done` no persistido en FR-11 → **patch** (`DiscoveryService` con `ActivityService` opcional + test).
- (edge-case) `set_status` con guard `no_fiable` a 0 filas aun emite evento/actividad → **medium**. → **patch** (`set_status` devuelve si actualizó; solo entonces transiciona + test de carrera).
- (blind-hunter) SSE sin cabeceras anti-buffering → **low**. → **patch** (`Cache-Control`/`X-Accel-Buffering`/`Connection`).
- (blind-hunter) `forbidden_lan` escribía en `activity_log` sin backoff (DoS de log) → **low-medium**. → **patch** (solo log, sin fila).
- (blind-hunter) `startEventStream` no reabría el stream tras completarse el job → **low**. → **patch** (`isActive` en vez de `!= null`).
- (verification-gap) asertos `endsWith` en el URL del SSE (ciegan la validación) → **low**. → **patch** (URL + Authorization exactos).
- (edge-case) path de fallback de `MachineListScreen` sin colector de prompts → **low** (rama no alcanzable en producción). → **defer** (deferred-work).
- (blind-hunter) notificación del fallback de polling con cuerpo genérico (no resuelve la acción) → **low**. → **defer** (el BE no expone la acción del último cambio, solo el origen; requeriría ampliar el DTO con `last_action`).
- (blind-hunter) MagicDNS/hostnames tailnet en `TransportSecuritySettings` → **low**. → **defer** (bind real y nombres en 4.1).

## Design Notes

- **Discriminador de token (`kind`):** columna nueva en `tokens` con default `'device'` (migración ALTER si falta, patrón `_migrate` existente). `token_exists` pasa a recibir `kind` con default `'device'` para no romper llamadas; el middleware de dispositivo exige `kind='device'`; el auth MCP exige `kind='mcp'`. `token create --kind mcp` (default device) mantiene los comandos actuales.
- **Eventos tipados:** el bus transporta un `type` (p. ej. `machine_online`/`machine_offline`/`machine_no_fiable`, `enroll_done`, `wake_sent`, `shutdown_done`, `scan_done`) + `origin`. La notificación de la app se dispara para eventos de origen `mcp` (tanto acciones `wake_sent`/`shutdown_done` como transiciones por `force_scan`/`shutdown`), resolviendo la atribución del wake (cuya transición real llega luego del loop de estado con origen `periodic`) sin mentir en el origen.
- **Wake y notificación:** `wake_machine` del MCP emite `wake_sent` con origen `mcp` (notifica "El agente IA encendió la máquina"); la transición offline→online posterior la emite el loop de estado con origen `periodic` y solo refresca la fila.
- **MCP sobre FastAPI:** el submount no ejecuta su lifespan → el host debe entrar `mcp.session_manager.run()` en el `lifespan` y `session_manager` solo existe tras llamar a `streamable_http_app()`. Montar antes de `include_router(api)` para que el Mount gane al catch-all 404. Auth MCP en la capa FastAPI (middleware/guard) antes del submount (AD-12).
- **Guard tailnet:** reutilizar el criterio de rangos propios de `adapters/net.py` (`127.0.0.0/8`, `100.64.0.0/10`, `fd7a:115c:a1e0::/48`) para aceptar clientes; `TransportSecuritySettings(allowed_hosts=...)` para el host header. El bind real de uvicorn queda para 4.1.
- **SSE:** `StreamingResponse` con generador async que se suscribe a una `asyncio.Queue` del bus y hace `yield f"data: {json}\n\n"`; al desconectar, cancelar la suscripción sin romper la emisión. El middleware de auth ya cubre `/api/v1/events`.
- **Notificación por polling (decisión del usuario):** `machines.last_origin` + `last_change_at` viajan en el DTO de `GET /machines`; la app guarda `(machine_id, last_change_at)` de los cambios ya notificados (por SSE o polling) y notifica solo cuando ve uno nuevo con `last_origin == "mcp"`. Los eventos SSE con origen `mcp` actualizan ese registro para que el siguiente poll no repita.
- **Permiso (decisión del usuario):** `POST_NOTIFICATIONS` se solicita al primer evento `mcp` (contextual); si se deniega, se registra el intento y se sugiere en ajustes sin volver a pedir en bucle.

## Verification

**Commands:**
- Backend: `cd backend && .venv/bin/python -m pytest -q` -- todos los tests verdes (incl. `test_events.py`, `test_mcp.py`); ojo al cuelgue preexistente al salir (usar `timeout`).
- App tests: `cd android && ./gradlew testDebugUnitTest` -- todos verdes
- App build: `cd android && ./gradlew assembleDebug` -- BUILD SUCCESSFUL
- CLI: `.venv/bin/wakemeup-cli token create --kind mcp <nombre>` y `token list` -- muestra el tipo; `token revoke` no afecta a los de dispositivo

**Manual checks (if no CLI):**
- Arrancar el BE y conectar un cliente MCP por loopback con token MCP: descubrir las 5 tools; sin token → rechazo. Verificación en tailnet/tablet como deferred-work si no hay ventana.
