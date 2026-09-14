---
title: 'Epic 2: Enciende y apaga — ciclo completo de control (stories 2.1–2.5)'
type: 'feature'
created: '2026-09-14'
status: 'done'
baseline_commit: '557df03'
route: 'dispatch'
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** El BE solo descubre máquinas y reporta estado; no puede darlas de alta (SSH), encenderlas (WOL) ni apagarlas. La app pinta botones de fila (Encender/Apagar/Dar de alta) que no hacen nada — "Disponible en la próxima versión".

**Approach:** Epic 2 completo en un solo encargo (decisión del usuario): implementar en el BE alta asyncssh con password de un solo uso (2.1), wake WOL (2.2), shutdown SSH con verificación de fingerprint (2.3), botones en la app (2.4) y gestión de tokens + logs de actividad (2.5). Cada story conserva sus propios tests (pytest / Robolectric) y commits independientes por story al finalizarla — el commit final del epic integra el sprint-status y cierra el epic.

## Boundaries & Constraints

**Always:**
- Contrato wire existente intacto: envelope de error `{"error":{code,message}}` (AD-1), `GET /api/v1/status` exento de auth, backoff 5/5 min → 429/15 min (AD-6).
- Arquitectura hexagonal: los servicios `enrollment`, `wake`, `shutdown` viven en `src/wakemeup/services/`; los adaptadores `net` (WOL) y `ssh` (asyncssh) en `src/wakemeup/adapters/`; el API nunca ejecuta acciones directamente (AD-4).
- La password del alta vive solo en memoria del proceso: nunca en disco, logs, argv, entorno ni cuerpo de respuesta (FR-6, AD-5, NFR-2). Se descarta también si el alta falla.
- WOL y SSH nunca atraviesan la VPN: el BE es siempre el emisor, residente en la LAN física (AD-3/AD-4).
- Config: secciones `[ssh]` y `[shutdown]` en config.toml (AD-1); la sección `[shutdown]` ya existe documentada en config.toml.example con `command = "sudo -n systemctl poweroff"`.
- Registro de actividad (2.5): timestamp, canal `api|mcp`, token, máquina, resultado; NUNCA passwords; computable SM-1..SM-4 sin atribución personal (FR-11).
- Errores con catálogo acotado (400/401/404/409/422/429/5xx) (FR-9).
- Tests automatizados de los AC de cada story (pytest en el BE; Robolectric/JVM en la app) y commit + push al finalizar cada story (requisito transversal).
- app: bilingüe ES/EN (strings XML), a11y ≥48dp, diálogo de apagado anunciado, Reduce Motion sin rotación (UX-DR8).

**Never:**
- Sin sshpass ni subprocesos: asyncssh en proceso obligatorio (FR-6, AD-5).
- Sin retry automático de WOL (un envío por petición, FR-4); el endpoint responde éxito y la confirmación llega del estado.
- Sin endpoints que expongan claves SSH (FR-8); la clave privada con permisos 600, usada solo por el servicio.
- NO introducir el token MCP/SSE (Es Epic 3), ni notificaciones del sistema (3.5), ni eventos del bus (AD-11) — el bus solo REGISTRA (deferred-work) pero es del Epic 3.
- No persistir la password ni los tokens planos (solo hash SHA-256 hex-lower, AD-6).
- No modificar el flujo de autenticación existente: el token MCP se añade en el Epic 3.
- No cambiar el contrato de `POST /scan` ni el DTO de máquina (AD-10) más allá de `managed` (ya existe).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Alta OK | POST /machines/1/enroll {usuario, password} | 200 {"id":1, "managed":true}; fingerprint + remote_user y authorized_keys instalados | N/A |
| Alta: password fallida | POST enroll con password errónea | 401 "Credenciales incorrectas"; máquina sigue managed=false; password descartada; log del evento | 401 envelope; la password nunca se loguea |
| Alta: máquina ya gestionada | POST enroll sobre managed=true | 409 "ya gestionada" (sin duplicar authorized_keys) | 409 conflict |
| Alta: máquina inexistente | POST enroll con id=999 | 404 not_found | 404 envelope |
| Wake: MAC inválida | POST /machines/1/wake con MAC malformada | 422 validation_error | 422 envelope |
| Wake: MAC multicast/cero | idem | 422 | 422 envelope |
| Wake: sin Ethernet | POST wake en host sin interfaz eth/en UP | 200 (éxito, un envío); GET /status con warning WOL | log warning; healthcheck warning (no bloquea) |
| Wake: máquina inexistente | id=999 | 404 | 404 envelope |
| Wake: máquina no gestionada | discovery-only máquina | 409 "no gestionada" (sin MAC fijada no se puede enviar WOL) | 409 conflict |
| Shutdown OK | POST /machines/1/shutdown, fingerprint coincide | 200 {"ok":true}; comando ejecutado; log canal api | N/A |
| Shutdown: fingerprint mismatch | host remoto cambió de clave | NO ejecuta; máquina → no_fiable persistido; evento registrado | 409 `conflict` "no_fiable" (decisión de planificación) |
| Shutdown: sin sudoers | remote_user sin NOPASSWD para el comando | error claro "sudo" | 502/503 envelope |
| Shutdown: máquina no gestionada / offline con TTL caducado | idem | 409 "no gestionada" (depende del estado persistido, no del TTL) | 409 |
| Tokens: list | CLI token list | nombres + estado activo/revocado | N/A |
| Tokens: revoke | CLI token revoke <name> o <hex> | revoca solo ese; los demás siguen activos | rc=1 si no existe (mantener 1.4) |
| Logs: shape | entrada de actividad | timestamp, canal, token, máquina, resultado; sin password | N/A |

</frozen-after-approval>

## Code Map

- `backend/src/wakemeup/core/models.py` — `Machine` y `MachineDTO` existentes: añadir `fingerprint`, `remote_user` (+ `state="no_fiable"` en `Machine`), y `managed` se calcula de `fingerprint IS NOT NULL`. NO romper `MachineDTO` (AD-10).
- `backend/src/wakemeup/adapters/db.py` — migración: `_COLUMN_MIGRATIONS` añadir `fingerprint` (TEXT) y `remote_user` (TEXT); SQL/query `get_by_id`, `set_managed`, `set_no_fiable`, `list_machines` (nuevos campos), tablas `keys` (fingerprint) — revisar diseño en implementación; `activity_log` (timestamp, canal, token, maquina, resultado). Seguir patrón `_migrate` existente (ALTER si falta).
- `backend/src/wakemeup/adapters/net.py` — `wol_interface()` ya existe (reporte 1.4). Añadir `send_wol(mac, interface=None)`: valida MAC (12 hex, no multicast, distinta de cero), construye 6×0xFF + MAC×16 y envía UDP broadcast (puerto 9/7, SO_BROADCAST); `interface=None` → usa la de `wol_interface()` o error claro.
- `backend/src/wakemeup/adapters/ssh.py` (NUEVO) — wrapper asyncssh: `connect(host,user,password)` (una sola vez en proceso, password en memoria), `read_host_fingerprint()` (SHA256:base64 de la host key), `install_authorized_key()` (SFTP idempotente: append si falta), `run_command(cmd)` (comando único, sin shell libre). Verificar fingerprint actual antes de cada acción (AD-2/AD-9).
- `backend/src/wakemeup/services/enrollment.py` (NUEVO) — flujo: conectar → leer fingerprint → copiar clave → set_managed. Sin password en disco.
- `backend/src/wakemeup/services/wake.py` (NUEVO) — valida MAC (422), envío único (net.send_wol), sin retry; devuelve éxito siempre.
- `backend/src/wakemeup/services/shutdown.py` (NUEVO) — fingerprint actual vs fijado; si no → `set_no_fiable` + log; si sí → run_command.
- `backend/src/wakemeup/services/activity.py` (NUEVO, 2.5) — registro de actividad (timestamp, canal, token, máquina, resultado) contra `activity_log`.
- `backend/src/wakemeup/api/__init__.py` — endpoints `POST /api/v1/machines/{id}/enroll`, `/wake`, `/shutdown` (body `{usuario, password}` para enroll; los otros sin body); auth middleware EXISTENTE aplica por defecto (el token es el de dispositivo); inyectar dependencias en `app.*` (grafo `_build_services`).
- `backend/src/wakemeup/cli/__init__.py` — (2.5) subcomandos `activity list` y `activity stat` (o similar) para consultar logs (decisión en implementación).
- `backend/src/wakemeup/config.py` — `[ssh]` y `[shutdown]` settings (2.1/2.3); `command` default `sudo -n systemctl poweroff`.
- `backend/config/config.toml.example` — documentar `[ssh]` (password de un solo uso, fingerprint) y `[shutdown]` (ya existe).
- `backend/tests/` — NUEVOS: `test_enrollment.py`, `test_wake.py`, `test_shutdown.py`, `test_activity.py` (pytest + dobles asyncssh via monkeypatch); ampliar `test_api.py` (endpoints nuevos con FakeDb/FakeNet extendidos: `get_by_id`, `set_managed`, `machine` fields).
- `android/app/src/main/java/com/wakemeup/manager/data/remote/WakemeupApi.kt` — métodos `enrollMachine(id,usuario,password)`, `wakeMachine(id)`, `shutdownMachine(id)` con el mismo patrón de envelope/`toApiException`.
- `android/app/src/main/java/com/wakemeup/manager/ui/machines/MachineListViewModel.kt` — `enroll()`, `wake()`, `shutdown()` con estado de acción `ActionState` (en vuelo/error), 401 → `sessionInvalid` existente; snackbar de resultados.
- `android/app/src/main/java/com/wakemeup/manager/ui/machines/MachineRow.kt` — botones ya renderizan (1.5): ahora pasan `onEnroll`/`onWake`/`onShutdown` reales; no_fiable sigue sin acciones (UX-DR5).
- `android/app/src/main/java/com/wakemeup/manager/ui/machines/MachineListScreen.kt` — diálogo de apagado obligatorio (UX-DR3) "¿Apagar <máquina>? No podrás acceder a ella…" con Cancelar/Apagar; diálogo de alta (UX-DR6): Usuario+Password con toggle, aviso "La password se usa una sola vez y no se guarda", CTA con progreso, error inline; snackbars.
- `android/app/src/main/res/values/strings.xml` + `values-en/strings.xml` — strings nuevos ES/EN de los diálogos y snackbars.
- `android/app/src/test/java/com/wakemeup/manager/...` — Robolectric: diálogo siempre visible, alta no persiste password, reintentos, snackbars (ampliar `MachineListScreenTest`/`MachineListViewModelTest`/`MachineRowTest` existentes o nuevos).

## Tasks & Acceptance

**Execution:**
- [x] `backend/src/wakemeup/adapters/ssh.py` (NUEVO) -- wrapper asyncssh con password en memoria, fingerprint SHA256:base64, authorized_keys idempotente, run_command -- AD-5/AD-9
- [x] `backend/src/wakemeup/core/models.py` + `adapters/db.py` -- campos fingerprint/remote_user + migración; tablas keys y activity_log -- AD-2/AD-7
- [x] `backend/src/wakemeup/adapters/net.py` -- `send_wol()` con validación MAC y broadcast -- FR-4
- [x] `backend/src/wakemeup/services/enrollment.py` `wake.py` `shutdown.py` `activity.py` (NUEVOS) -- lógica de control + registro -- AD-4
- [x] `backend/src/wakemeup/api/__init__.py` + `cli/__init__.py` + `config.py` + `config.toml.example` -- endpoints y CLI/logs -- FR-7/FR-11
- [x] `backend/tests/test_enrollment.py` `test_wake.py` `test_shutdown.py` `test_activity.py` (NUEVOS) + ampliar `test_api.py` -- AC 2.1–2.5 pytest
- [x] `android/.../WakemeupApi.kt` -- enroll/wake/shutdown client -- contrato BE
- [x] `android/.../MachineListViewModel.kt` -- acciones con estado y 401 -- FR-13
- [x] `android/.../MachineListScreen.kt` `MachineRow.kt` -- diálogos y botones reales -- UX-DR3/DR5/DR6
- [x] `android/.../strings.xml` (ES/EN) + tests Robolectric -- diálogos, alta, reintentos -- UX-DR8/DR9
- [x] sprint-status.yaml -- marcar 2.x a done tras cada story y epic-2 done al cierre -- proceso BMAD

**Acceptance Criteria:**
- Given el BE con su par de claves Ed25519 y config `[ssh]`, when se ejecuta el alta con password, then asyncssh en memoria, fingerprint fijado, authorized_keys idempotente, managed=true (FR-6; AC 2.1)
- Given password fallida, then 401 "Credenciales incorrectas", máquina sigue descubierta, password descartada (AC 2.1)
- Given máquina gestionada offline, when wake, then MAC validada (422 si no), UN solo magic packet al broadcast (6×0xFF+MAC×16) por interfaz Ethernet si existe; sin Ethernet → GET /status warning; sin retry; 200 aunque tarde en arrancar (FR-4/FR-5; AC 2.2)
- Given máquina gestionada online con fingerprint fijado, when shutdown, then verifica fingerprint; si coincide ejecuta `sudo -n systemctl poweroff` vía asyncssh; si NO coincide → no ejecuta, marca `no_fiable`, evento registrado; sin sudoers → error claro; log canal `api` (FR-7/AD-2; AC 2.3)
- Given la app con lista, when toco Encender/Apagar/Dar de alta, then wake/shutdown/enroll reales; diálogo de apagado SIEMPRE; alta con password no persistida; reintento sin salir de pantalla; snackbar resultados; no_fiable sin acciones destructivas (FR-13/UX-DR3/DR5/DR6; AC 2.4)
- Given la CLI, when genero/revoco tokens, then token mostrado una sola vez, solo hash SHA-256 hex-lower, revocación sin afectar otros; backoff 5/5 min → 429/15 min en CLI y API; logs con shape FR-11 sin passwords (AC 2.5)
- Given tests, then pytest (BE) y Robolectric (app) verdes para los cases de la matriz; commit + push por story (AC transversal)

## Implementation Notes

- Decisión de ruta: en la DB se guardan `fingerprint` y `remote_user` en `machines`; `managed` se DERIVA de `fingerprint IS NOT NULL` en el DTO (no columna nueva).
- El estado `no_fiable` se persiste en `machines.state` (campo existente) — no columna nueva. El front lo pinta como badge ámbar y las acciones destructivas se ocultan (AD-10).
- La validación de MAC del wake: `len==12` o `len==17` con separadores `:`/`-` → normalizar; multicast = bit LSB del primer octeto (0x01); cero = `00:00:00:00:00:00`.
- `send_wol` con `SO_BROADCAST` y destino `255.255.255.255:9` (o la IP broadcast de la subred física si es determinable); fallback puerto 7.
- asyncssh: fingerprint = `SHA256:<base64>` de la host key real (formato OpenSSH). Comparación del fingerprint fijado con el actual en cada shutdown — nunca confiar en known_hosts.
- La CLI de logs (2.5) puede ser `wakemeup-cli activity list [--limit N]` y `wakemeup-cli activity stat` — decidir forma exacta en implementación, sin romper `token create/revoke/list` existentes.
- Robolectric: el diálogo de apagado se testea con `TestableHost`/ComposeUI; el alta no persiste la password (assert que `SettingsRepository`/SecureStore no recibe password).
- Al terminar cada story: actualizar `sprint-status.yaml` (story 2.x → done), commit + push; el último commit cierra el epic (epic-2 → done).

## Spec Change Log

<!-- Append-only. Empty until the first review loopback. -->

## Review Triage Log

- (blind-hunter) client_keys=export_public_key() bytes (ssh.py:82-84) → asyncssh KeyImportError en producción; verificado con asyncssh.generate_private_key+SSHClientConnectionOptions → **high**: el alta/shutdown no funcionarían con clave real. → **patch**
- (blind-hunter) remote_home "~" (ssh.py:52, config.toml.example:41) → SFTP no expande `~` → **medium**: error en despliegue con el ejemplo. → **patch** (resolver en config/example; el adaptador documenta que se requieren rutas absolutas)
- (blind-hunter) install/rewrite de authorized_keys sin fijar permisos 0o600 (ssh.py:146-177) → **medium**: umask del remoto puede dejar mode aceptado por OpenSSH de forma frágil. → **patch** (setstat 0o600 tras escribir)
- (edge-case) record_activity sin begin/commit en los caminos de error de enroll (409/401/502), wake (409/422/sin-eth) y shutdown (409/502) → **high** (verificado con DB real: `OperationalError: cannot start a transaction within a transaction` cuando precede a otra transacción explícita; el loop de estado/el siguiente control rompe). → **patch** (envolver registros en begin/commit, como el camino de éxito)
- (edge-case) send_wol RuntimeError cuando ambos puertos (9 y 7) fallan → WakeService solo captura NoWolInterfaceError/OSError → el contrato "éxito degradado" se rompe como 500 (verificado: `_WOL_PORTS` loop deja last_error y lanza RuntimeError sin catch en wake.py) → **high**. → **patch**
- (blind-hunter/edge-case) set_status/clobbering de `no_fiable` por el loop de estado: verificado con DB real — tras `set_managed`/`set_no_fiable`, el siguiente `check_all` sobrescribe con online/offline → el badge de la app nunca persiste (AD-10/UX-DR5 violado) → **high**. → **patch** (guardar en update `WHERE state != 'no_fiable'` o mantener no_fiable; test de regresión)
- (verification-gap) managed=true nunca se ejercita de verdad (test_api solo aserta managed=false; FakeStatus no siembra fingerprint) → **medium**: derivación `fingerprint IS NOT NULL` sin pin de la rama verdadera. → **patch** (extender FakeStatus/test con fingerprint → managed=true)
- (verification-gap) shutdown 409 "conflict" (cierra diálogo + snackbar de error) sin test que lo ejecute; `wake fallido` sí cubre el path análogo → **low** (el path de wake testea el mecanismo; falta el específico de shutdown). → **patch** (test ViewModel shutdown-fail)
- (verification-gap) refresh(silent=true) tras control sin test que aserte el GET posterior; `enroll fallido`/`wake` tampoco lo pin → **medium** (FR-2: el estado real llega del BE; regresión no detectada). → **patch** (test: GET /machines tras éxito)
- (blind-hunter) ActivityService construido dos veces en `_build_services` (api/__init__.py:35-46) → la instancia inyectada en los 3 servicios difiere de `app.activity` — mismo estado (db), pero cableado redundante que invita a drift → **low**. → **patch** (reusar la misma instancia)
- (blind-hunter) `_EnrollBody` solo `min_length=1` sin strip ni tope → usuario/password vacíos llegan a asyncssh → **low-medium** → **patch** (strip_whitespace + max_length razonable, p. ej. 1024)
- (blind-hunter) `run_command` mapea cualquier exit 255 a "sudo …" (ssh.py:192-195) → diagnóstico engañoso para fallos no-sudo → **low**. → **patch** (solo mapear 255 si stderr contienen "sudo" o el mensaje del exit; si no, motivo genérico)
- (edge-case) doble enroll TOCTOU (enrollment.py:62-106): dos altas concurrentes → ambas pasan el check → `keys.machine_id UNIQUE` → record_key 2ª → IntegrityError 500 (concurrentes) — **medium-low** (ventana estrecha; el BE es de un solo usuario/cola API; aún así real) → **patch** (transacción + re-check dentro antes de record_key; o catch de IntegrityError → 409). El catch dentro de la transacción ya existe (rollback) — el fix está en comprobar de nuevo dentro de la misma transacción.
- (edge-case) whitespace-only usuario/password → 401 "credenciales" en lugar de 422 (verificado: Field(min_length=1) acepta "   ") → **low**. → **patch** (strip en el servicio/body)
- (edge-case) activity_log/keys DDL duplicado en `_migrate` vs `_SCHEMA` (db.py:118-146) → dos fuentes de verdad → **low** (drift futuro). → **patch** (reusar constantes)
- (blind-hunter) la password del alta no se persiste — la app ROBO testea solo cierre de diálogo, no repositorio/body → **low-medium** (el body del POST sí se aserta en `enroll envia usuario y password en el POST`; el almacén no se ejercita) → **defer** (verificación de non-persistence en almacén real = Keystore, deferred-work existente)
- (blind-hunter) send_wol solo broadcast 255.255.255.255 sin fallback a broadcast dirigido → **medium-low**: algunas LAN filtran global broadcast → el "Encendido enviado" miente. → **defer** (mejora de red; no rompe el contrato; verificación en LAN real al instalar)
- (blind-hunter) sin recovery para `no_fiable` (re-enroll → 409; no hay forma de re-fijar) → **medium** (estado que el propio sistema crea sin salida UX; el alta repetida fallaría hasta borrar la fila) → **defer** (requiere diseño de UX/cli de re-enroll; fuera del AC)
- (edge-case) ActivityService de los 3 servicios: `record()` con canal/token, y el de app es el mismo objeto → ya cubierto
- (edge-case) La pantalla: `LaunchedEffect(actionMessages.size)` → si un segundo mensaje llega durante el showSnackbar se cancela la snackbar visible → **low-medium** → **patch** (mover el showSnackbar a efecto con lastMessage o encolar en estado)
- (edge-case) shut-down 409 mientras el diálogo queda abierto: el 502/404 con error snackbar tras cerrar; el path "conflict" se testeará en el patch de shutdown — ver `(verification-gap) shutdown 409`

## Design Notes

- **Fingerprint en alta:** capturado por `read_host_fingerprint()` ANTES de instalar la clave (no puede haber MITM entre la lectura y la instalación si el fingerprint se captura en la misma conexión). Comparación posterior estricta en cada shutdown (AD-2/AD-9).
- **Decisiones de planificación (resueltas por mí, sin implicar al usuario):** operación sobre máquina no gestionada → 409 `conflict` "no gestionada" (enroll/wake/shutdown); fingerprint mismatch en shutdown → 409 `conflict` con mensaje claro (envelope existente, el front distingue por code); canal del registro de actividad para los 3 endpoints → `api`.
- **Enroll idempotente:** authorized_keys = append si falta (comprobar `grep` de la clave o leer el fichero y buscar); `set_managed` idempotente. Un enroll repetido sobre managed no duplica (409).
- **Wake sin Ethernet:** `wol_interface()` responde `None` → envío falla con warning; no bloquea el resto. El healthcheck ya reporta warning (1.4).
- **DTO managed:** hoy `machines()` hardcodea `managed=False`; pasa a derivar de fingerprint. El front ya renderiza según managed (1.5).

## Verification

**Commands:**
- Backend: `cd backend && uv run pytest -q` -- todos los tests verdes (incl. los nuevos de 2.1–2.5)
- Compilar app: `cd android && ./gradlew assembleDebug` -- BUILD SUCCESSFUL
- Tests app: `cd android && ./gradlew testDebugUnitTest` -- todos verdes
- CLI: `uv run wakemeup-cli token list` -- salida esperada; `uv run wakemeup-cli activity list` -- registros
- Manual (si hay tiempo): `POST /api/v1/machines/1/wake` con MAC inválida → 422; shutdown con fingerprint distinto → no_fiable

**Manual checks (if no CLI):**
- (Ninguno — los tests cubren la matriz; verificación en dispositivo queda como deferred-work)
