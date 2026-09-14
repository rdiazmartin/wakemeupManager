# Epic 2 Context: Enciende y apaga — ciclo completo de control

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Tras este epic, el usuario puede controlar por completo cada máquina de su red desde la app Android: dar de alta una máquina descubierta una sola vez con usuario y password SSH (que nunca se guardan), encenderla por WOL y apagarla por SSH — todo protegido con tokens de dispositivo por móvil, verificación de fingerprint de host y registro de actividad. El BE completa su superficie de control (alta, wake, shutdown), la app expone los botones Encender/Apagar/Dar de alta, y el administrador dispone de la CLI para gestionar tokens y consultar los logs de actividad.

## Stories

- Story 2.1: Alta de máquinas con password de un solo uso (asyncssh)
- Story 2.2: Encendido por WOL (magic packet al broadcast local)
- Story 2.3: Apagado por SSH con verificación de fingerprint
- Story 2.4: Botones Encender / Apagar / Alta en la app (UX-DR3, UX-DR5, UX-DR6)
- Story 2.5: Gestión de tokens y registro de actividad (CLI + logs)

## Requirements & Constraints

- El alta conecta por asyncssh en proceso (sin sshpass, sin subprocesos): la password vive solo en memoria del proceso, nunca en disco, logs, argv ni entorno, y se descarta también si el alta falla.
- El alta fija en la máquina el fingerprint de host (`SHA256:base64`), el usuario remoto (`remote_user`) y copia la clave pública del BE a `~/.ssh/authorized_keys` de forma idempotente (append si falta). La máquina pasa a `managed: true`.
- El wake valida la MAC (12 hex, no multicast, distinta de cero → 422 si inválida), envía un único magic packet (6×0xFF + MAC ×16) al broadcast de la subred física por la interfaz Ethernet si existe; sin Ethernet, `GET /status` reporta `warning` (WOL no fiable vía WiFi) sin bloquear el resto. Sin reintento automático; el endpoint responde éxito y la confirmación real llega del estado.
- El shutdown solo se ejecuta si el fingerprint actual coincide con el fijado; si no, la máquina pasa a `no_fiable` sin ejecutar nada. El comando es el de `[shutdown] command` (default `sudo -n systemctl poweroff`), sin shell libre; el alta documenta el prerequisito de `sudoers NOPASSWD` restringido.
- Autenticación de la API por token de dispositivo: tokens de 32 B hex mostrados una sola vez, almacenados solo como hash SHA-256 de la representación hex-lower; revocables individualmente; 5 fallos/5 min → 429 durante 15 min (contadores en memoria, se resetean al reiniciar). El token MCP es independiente de los de dispositivo (mismo formato y revocación).
- Registro de actividad con timestamp, canal `api|mcp`, token, máquina y resultado; nunca passwords; permite computar las métricas SM-1..SM-4 sin atribución personal.
- La clave privada SSH del BE tiene permisos 600, la usa solo el servicio como usuario no-root; ningún endpoint expone claves.
- Toda la API escucha solo en la interfaz tailnet + loopback; errores con envelope uniforme `{"error": {"code", "message"}}` (400/401/404/409/422/429/5xx).
- NFR transversal de proceso: cada story requiere tests automatizados de sus criterios de aceptación (pytest en el BE, Robolectric/tests JVM en la app) y commit + push obligatorio al finalizar.
- WOL y SSH nunca atraviesan la VPN: el BE es siempre el emisor, residente en la LAN física.

## Technical Decisions

- Arquitectura hexagonal en el BE: servicios `enrollment`, `wake`, `shutdown` en `src/wakemeup/services/`; adaptadores `net` (WOL) y `ssh` (asyncssh) en `src/wakemeup/adapters/`; el API no ejecuta acciones directamente (AD-4).
- Identity machine (AD-2, AD-9): id entero PK + `host_fingerprint` fijado en el alta + `remote_user`; la IP es efímera. El shutdown usa exactamente `remote_user` + par Ed25519 del BE + fingerprint fijado.
- Endpoints del contrato: `POST /api/v1/machines/{id}/enroll` (body: usuario, password), `POST /api/v1/machines/{id}/wake`, `POST /api/v1/machines/{id}/shutdown`; `GET /api/v1/status` expone versión e interfaz WOL (AD-1, FR-9).
- DTO de máquina en el wire incluye `status ∈ {online, offline, no_fiable}` y `managed ∈ {true, false}`; la UI nunca deduce `no_fiable` (AD-10).
- Persistencia en SQLite (aiosqlite): inventario, hashes de tokens, fingerprints y registro de actividad; clave SSH y config fuera de la DB con 600/700 (AD-7).
- Stack fijado: Python 3.14 vía uv, FastAPI 0.141, asyncssh 2.24, aiosqlite 0.22; app Kotlin 2.4.20, Compose BOM 2026.09.00, Ktor 3.5.2, targetSdk 36/minSdk 26; token de la app en Keystore + Encrypted DataStore (AD-8, NFR-7).
- Errores con catálogo acotado y cuerpo uniforme; config en `config.toml` con secciones `[ssh]` y `[shutdown]` (AD-1).

## UX & Interaction Patterns

- Fila de máquina (Machine row) con acciones inline a la derecha según estado: descubierta → `Dar de alta`; gestionada/offline → `Encender` (tonal, acento); gestionada/online → `Apagar` (borde); `no_fiable` → badge ámbar "No fiable" sin acciones destructivas. Estados siempre punto + texto, nunca solo color (UX-DR2, UX-DR5).
- Apagado: diálogo de confirmación obligatorio y único "¿Apagar <máquina>? No podrás acceder a ella…" con Cancelar/Apagar; nunca tap silencioso. Confirma con loader en el botón (UX-DR3, Flow 3).
- Alta: dialog con campos Usuario + Password (toggle de visibilidad), aviso "La password se usa una sola vez y no se guarda", CTA "Dar de alta" con progreso y error inline con motivo (password a reintroducir) (UX-DR6, Flow 2).
- Acciones fallidas permiten reintento sin salir de la pantalla; snackbar solo para acciones propias ("Encendido enviado", "Máquina apagada", errores con motivo humano) (UX-DR7, UX-DR9).
- Accesibilidad: targets ≥48dp, fila anuncia nombre + estado + acciones con TalkBack, diálogo de apagado anunciado; Reduce Motion sin rotación (UX-DR8).
- Strings ES/EN externas desde v1; microcopy sin jerga técnica ("Despierta tu máquina desde cualquier lugar", "Máquina no responde: ¿está encendida y con SSH activo?").

## Cross-Story Dependencies

- 2.1 depende del inventario y del par de claves del BE (Epic 1, Story 1.1) y de la config `[ssh]`; habilita 2.3 (sin `managed` no hay apagado).
- 2.2 usa la MAC registrada por el descubrimiento (Epic 1) y la interfaz/reporting de `GET /status` (Story 1.1); el estado de arranque lo confirma el servicio de estado de Epic 1 (FR-2).
- 2.3 depende de 2.1 (fingerprint fijado + clave instalada) y del DTO `no_fiable` de la API (Story 1.4 / AD-10).
- 2.4 consume el DTO completo de máquina (Story 1.4) y los endpoints de 2.1–2.3; el alta en la app depende del contrato `enroll` del BE.
- 2.5 se apoya en la CLI del BE (Story 1.1) y en la infraestructura de auth de la API (Story 1.4); los logs de canal `api|mcp` alimentan las métricas SM-1..SM-4.
- Los eventos del bus (AD-11, Epic 3) registran y publican altas, wakes y shutdowns de este epic desde cualquier origen.
