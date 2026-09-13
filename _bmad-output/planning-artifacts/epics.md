---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics, step-03-create-stories-epic1, step-03-create-stories-epic2, step-03-create-stories-epic3, step-03-create-stories-epic4, step-04-final-validation]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-wakemeupManager-2026-09-13/prd.md
  - _bmad-output/planning-artifacts/architecture/architecture-wakemeupManager-2026-09-13/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/ux-designs/ux-wakemeupManager-2026-09-13/DESIGN.md
  - _bmad-output/planning-artifacts/ux-designs/ux-wakemeupManager-2026-09-13/EXPERIENCE.md
---

# wakemeupManager - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for wakemeupManager, decomposing the requirements from the PRD, UX Design if it exists, and Architecture requirements into implementable stories. Incluye los requisitos transversales de calidad declarados por Roberto en la sesión: cobertura de tests en todo el código (Robolectric en la app) y commit+push obligatorio al final de cada story.

## Requirements Inventory

### Functional Requirements

- FR-1: El BE escanea el rango de IPs configurado en la LAN física (ping + /proc/net/arp, no-root) y registra IP, MAC y hostname de cada máquina que responde. (4.1)
- FR-2: El BE mantiene el estado online/offline de cada máquina (TTL 60 s default, 15–300 s; comprobación TTL/2). (4.1)
- FR-3: El BE re-dispara el descubrimiento cada 10 min (configurable) y permite forzarlo vía POST /scan desde la app; ausencias → offline, sin borrado salvo DELETE explícito (no en UI v1). (4.1)
- FR-4: El BE envía el magic packet WOL al broadcast de la subred física, con MAC validada (422 si inválida), un envío por petición, sin retry automático. (4.2)
- FR-5: El BE usa interfaz Ethernet para WOL si existe; si no, reporta warning en GET /status; documenta WOL en firmware. (4.2)
- FR-6: Alta con password de un solo uso vía asyncssh en proceso (nunca persistida), fijación de fingerprint, copia idempotente de clave vía SFTP a authorized_keys. (4.3)
- FR-7: Apagado por SSH con clave del BE, solo si el fingerprint coincide; si no, no_fiable y sin apagado; comando Linux restringido (sudo -n systemctl poweroff, sudoers NOPASSWD documentado). (4.3)
- FR-8: Clave SSH del BE con permisos 600, uso exclusivo del servicio como usuario no-root; sin endpoints que expongan claves. (4.3)
- FR-9: Superficie de API REST (catálogo de endpoints + errores con código HTTP) escuchando solo tailnet + loopback. (4.4)
- FR-10: Autenticación por token de dispositivo (uno por móvil, revocable, backoff 5 fallos/5 min → 429/15 min). (4.4)
- FR-10b: Token dedicado del MCP, independiente de los de dispositivo, mismo formato y revocación. (4.4)
- FR-11: Registro de actividad completo (timestamp, canal api|mcp, token, máquina, resultado); los logs permiten medir SM-1..SM-4; nunca passwords. (4.4)
- FR-12: App lista máquinas con estado (polling 30 s default 10–300 s, pull-to-refresh, escanear ahora); fallos de conexión sin romper el listado. (4.5)
- FR-13: Botones Encender (offline) / Apagar (gestionada online, con confirmación); Alta en descubiertas; reintento sin salir de pantalla. (4.5)
- FR-14: Alta desde la app: usuario + password de un solo uso, no persistida en el dispositivo. (4.5)
- FR-15: Configuración URL + token, validación contra GET /status (401 → "token rechazado", timeout → "BE inalcanzable"); token en keystore; sesión sin re-login salvo 401. (4.5)
- FR-16: Servidor MCP (contrato estándar) con tools list_machines, get_machine_status, wake_machine, shutdown_machine, force_scan; sin tools de alta. (4.6)
- FR-17: MCP escucha solo tailnet + loopback y exige su token dedicado; generación/revocación por CLI. (4.6)
- FR-18: Eventos push SSE de cambios de estado a la app (≤2 s con stream; polling como fallback; backoff de reconexión; pausa en ahorro de batería). (4.6)

### NonFunctional Requirements

- NFR-1 (Seguridad): El BE escucha solo en la interfaz tailnet y loopback; el tráfico WOL y SSH nunca atraviesan la VPN (AD-6, FR-9/17).
- NFR-2 (Seguridad): Passwords SSH solo en memoria del proceso durante el alta; nunca en disco, logs, argv o entorno (FR-6/11, AD-5).
- NFR-3 (Seguridad): Tokens almacenados como hash SHA-256 (pre-imagen hex-lower); rate-limit de autenticación (AD-6).
- NFR-4 (Seguridad): Pinning de fingerprint de host en el alta; apagados solo si el fingerprint coincide (AD-2, AD-9).
- NFR-5 (Fiabilidad): Estado con TTL 60 s (15–300 s) comprobado a TTL/2; poll de la app 30 s (10–300 s); reconexión SSE con backoff (FR-2/12/18).
- NFR-6 (Fiabilidad): Escaneos serializados (singleflight); inventario no destructivo (FR-1/3, AD-3).
- NFR-7 (Seguridad app): Token almacenado en Keystore + Encrypted DataStore (EncryptedSharedPreferences excluida por deprecación) (FR-15, AD-8).
- NFR-8 (Accesibilidad): Estados nunca solo por color (punto+texto); TalkBack anuncia rol+estado; targets ≥48dp; Dynamic Type sin truncar (EXPERIENCE.md).
- NFR-9 (i18n): Strings ES/EN externas desde v1 (EXPERIENCE.md, AD-8).
- NFR-10 (Batería): Polling pausable en ahorro de batería; stream SSE pausable igualmente (FR-12/18).

### Additional Requirements

- [Técnico/Arquitectura] Paradigma hexagonal en BE (núcleo + adaptadores api/db/ssh/net/cli) y MVVM+UDF en la app (AD-1, AD-8).
- [Técnico/Arquitectura] Stack fijado: Python 3.14 vía uv, FastAPI 0.141, aiosqlite 0.22, asyncssh 2.24, SDK mcp 2.2, Kotlin 2.4.20, Compose BOM 2026.09.00 (M3 1.4.0), AGP 9.4/Gradle 9.7, targetSdk 36/minSdk 26, Ktor 3.5.2, security-crypto 1.1/DataStore 1.2.1.
- [Técnico/Arquitectura] Contrato de API verbatim: GET/POST/DELETE sobre /api/v1 (machines, scan, events SSE, mcp), error envelope {"error": {"code", "message"}} (AD-1).
- [Técnico/Arquitectura] Identidad de máquina: id entero PK, fingerprint SHA256:base64, remote_user; IP efímera; estado no_fiable (AD-2, AD-10).
- [Técnico/Arquitectura] Alta sin sshpass: asyncssh en proceso, SFTP append idempotente a authorized_keys (AD-5).
- [Técnico/Arquitectura] Eventos: bus asyncio pub/sub + SSE en /api/v1/events con Bearer (AD-11).
- [Técnico/Arquitectura] MCP con SDK oficial montado en FastAPI, token MCP validado en capa FastAPI (AD-12).
- [Técnico/Arquitectura] Persistencia SQLite (aiosqlite); claves y config fuera de la DB con 600/700 (AD-7).
- [Despliegue/Operaciones] Despliegue con systemd (Restart=always), usuario dedicado wakemeup, healthcheck vía GET /status, install/upgrade idempotente, Python de uv explícito (spine, Entorno operacional).
- [Calidad/Proceso — REQUISITO DEL USUARIO] TODO el código debe estar cubierto de tests: BE con pytest (unidades de servicios + adaptadores + API con cliente de pruebas), app Android con tests JVM + Robolectric (ViewModels, repositorios, flujos de Compose), y tests de integración del contrato API. Definición de done de cada story incluye cobertura de los criterios de aceptación con al menos un test automatizado.
- [Calidad/Proceso — REQUISITO DEL USUARIO] Al final de CADA story se realiza commit y push obligatorios (convención: commit atómico por story, mensaje descriptivo en español, sin auto-commit en mitad del trabajo).

### UX Design Requirements

- UX-DR1: Implementar el tema oscuro por defecto con Material 3 + Dynamic Color (Material You); fallback a paleta grafito/acento verde eléctrico (tokens de DESIGN.md: surface-base #121417, surface-raised #1A1D21, ink, accent #2DE0A5, success/warning/danger).
- UX-DR2: Implementar la fila de máquina (Machine row) como componente: indicador de estado punto 10dp + texto meta, nombre en title, IP/MAC en meta monoespaciada, acciones inline a la derecha (Encender tonal / Apagar con borde / Dar de alta).
- UX-DR3: Dialog de apagado con advertencia obligatorio y único (modal md 16px, texto "¿Apagar <máquina>? No podrás acceder a ella…", Cancelar/Apagar); nunca un tap silencioso.
- UX-DR4: Pantalla/estado de primer arranque sin configurar (explicación + botón Configurar); banner persistente de token inválido (401); lista cacheada con badge "Sin conexión"; estado vacío con mensaje y acción de escaneo.
- UX-DR5: Estados de fila conforme AD-10: descubierta (solo Alta), gestionada/online (Apagar), gestionada/offline (Encender inactivo si online), no_fiable (badge ámbar, sin acciones destructivas); nunca solo color (punto+texto).
- UX-DR6: Flujo de alta (dialog/sheet): campos Usuario + Password con toggle de visibilidad, aviso "La password se usa una sola vez y no se guarda", CTA Dar de alta con progreso, error inline con motivo (password a reintroducir).
- UX-DR7: Reflejo de eventos SSE en la fila sin snackbar para acciones ajenas (solo las propias muestran snackbar); sin indicador de conexión en vivo en v1; polling de 30 s como fallback (Flow 4).
- UX-DR8: Accesibilidad: estados punto+texto siempre; TalkBack anuncia fila con rol+estado; diálogo de apagado anunciado; targets ≥48dp; Dynamic Type al máximo sin truncar; Reduce Motion (sin rotación del icono de escaneo → texto "Escaneando…").
- UX-DR9: Snackbars bilingües conforme Voice: "Despierta tu máquina desde cualquier lugar", "Máquina no responde: ¿está encendida y con SSH activo?", "Token rechazado — revisa los ajustes"; strings ES/EN externas.

### FR Coverage Map

FR-1: Epic 1 - Escaneo del rango por ping + /proc/net/arp
FR-2: Epic 1 - Estado online/offline con TTL
FR-3: Epic 1 - Escaneo periódico 10 min + forzado POST /scan
FR-4: Epic 2 - Envío de magic packet WOL al broadcast local
FR-5: Epic 1 - Interfaz Ethernet para WOL y warning en GET /status
FR-6: Epic 2 - Alta con password de un solo uso (asyncssh)
FR-7: Epic 2 - Apagado por SSH con verificación de fingerprint
FR-8: Epic 2 - Clave del BE con permisos 600, usuario no-root
FR-9: Epic 2 - Superficie de API REST (catálogo, solo tailnet+loopback)
FR-10: Epic 2 - Autenticación por token de dispositivo
FR-10b: Epic 2 - Token dedicado del MCP
FR-11: Epic 2 - Registro de actividad con canal api|mcp
FR-12: Epic 1 - Listado de máquinas con estado y escanear ahora
FR-13: Epic 2 - Botones Encender/Apagar/Alta
FR-14: Epic 2 - Alta desde la app
FR-15: Epic 2 - Configuración de conexión y sesión
FR-16: Epic 3 - Servidor MCP con tools (sin alta)
FR-17: Epic 3 - Acceso MCP solo tailnet con token dedicado
FR-18: Epic 3 - Eventos push SSE a la app

NFR-1..NFR-10: Transversales (seguridad/fiabilidad/app se aplican en los epics que tocan su dominio; NFR-8/9/10 con peso en Epic 4)
Requisitos de proceso (tests + commit/push): transversales a todas las stories


## Epic List

### Epic 1: Descubre tu red
Después de este epic, el usuario abre la app y ve todas las máquinas de su red con su estado, actualizado solo, pudiendo forzar un escaneo desde el móvil.
**FRs covered:** FR-1, FR-2, FR-3, FR-5 (parcial: WOL Ethernet/warning), FR-12

### Epic 2: Enciende y apaga — ciclo completo de control
Después de este epic, el usuario da de alta una máquina (una sola vez, con credenciales que nunca se guardan), la enciende por WOL y la apaga por SSH, todo desde la app Android, con seguridad de token por dispositivo.
**FRs covered:** FR-4, FR-5, FR-6, FR-7, FR-8, FR-9, FR-10, FR-10b, FR-11, FR-13, FR-14, FR-15

### Epic 3: Agente IA en la red — MCP y eventos en vivo
Después de este epic, un agente IA conectado desde dentro de la VPN consulta y controla las máquinas vía MCP, y cualquier cambio de estado (suyo, de la app o del escaneo) se refleja al instante en la app Android.
**FRs covered:** FR-16, FR-17, FR-18 (3.5 notificación MCP)

### Epic 4: Lanzamiento pulido — distribuir la herramienta con confianza
Después de este epic, la app se distribuye públicamente: primer arranque guiado, estados de error claros, accesibilidad completa, bilingüe ES/EN y despliegue operacional del BE robusto (systemd, install/upgrade, healthcheck).
**FRs covered:** UX-DR4, UX-DR7, UX-DR8, UX-DR9, NFR-7/8/9/10 (peso), requisitos de despliegue de Arquitectura


## Epic 1: Descubre tu red

Tras este epic, el usuario abre la app y ve todas las máquinas de su red con su estado, actualizado solo, pudiendo forzar un escaneo desde el móvil.

### Story 1.1: Esqueleto del BE — hexagonal, config y despliegue base

As a desarrollador del backend,
I want el esqueleto del BE con la forma hexagonal, configuración y unidad systemd,
So that las stories siguientes construyen sobre una base consistente y desplegable.

**Acceptance Criteria:**

**Given** el repo backend/ vacío
**When** se inicializa el proyecto con `pyproject.toml` (uv, Python 3.14) y la estructura `src/wakemeup/{api,services,adapters,core,cli}`
**Then** el esqueleto respeta la forma hexagonal del AD-1 (adaptadores → servicios → núcleo) sin dependencias circulares
**And** `config.toml.example` expone las secciones `[scan] [api] [ssh] [auth]` con los defaults del spine (scan 10 min, TTL 60 s, backoff 5/5 min → 429/15 min)
**And** existe la unidad `deploy/wakemeup.service`: usuario dedicado `wakemeup`, `Restart=always`, `Wants=network-online.target`, Python de `uv` explícito
**And** `GET /status` (healthcheck mínimo) responde 200 con versión
**And** existe al menos un test pytest que instancia la app y verifica `/status` (Cobertura de tests: requisito transversal)
**And** se realiza commit + push al completar la story (requisito transversal)

**Refs:** Arquitectura AD-1/AD-7 (hexagonal, SQLite + claves 600/700, stack), config del spine Entorno operacional, NFR-2/3

### Story 1.2: Descubrimiento de máquinas (escaneo del rango)

As a usuario,
I want que el BE escanee el rango de IPs configurado y registre IP, MAC y hostname de cada máquina,
So that vea automáticamente qué máquinas hay en mi red.

**Acceptance Criteria:**

**Given** el rango de IPs configurado en `[scan]` y el BE corriendo en la LAN física
**When** se ejecuta el descubrimiento (periódico cada 10 min configurable, o forzado)
**Then** el inventario registra todas las máquinas que responden a ICMP o están en `/proc/net/arp`, con MAC válida y hostname si resuelve (FR-1)
**And** las interfaces propias del BE (incluida tailnet y loopback) y direcciones no-LAN se excluyen del inventario (AD-3)
**And** los escaneos se serializan (singleflight): un escaneo en curso no se duplica ni corrompe el inventario (AD-3)
**And** las ausencias marcan la máquina como offline SIN borrarla del inventario (FR-3)
**And** la técnica no requiere root: ping binario + `/proc/net/arp` (AD-3)
**And** tests pytest cubren el servicio discovery (singleflight, exclusión, upsert) y el adaptador net (doble de ping/ARP) — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 1.3: Estado online/offline con TTL

As a usuario,
I want ver el estado actual (online/offline) de cada máquina, actualizado solo,
So that sepa si una máquina está encendida sin entrar por SSH.

**Acceptance Criteria:**

**Given** máquinas en el inventario
**When** el BE comprueba el estado periódicamente (cada TTL/2, TTL default 60 s configurable 15–300 s)
**Then** el estado de cada máquina se actualiza según respuestas de ICMP/ARP (FR-2)
**And** el estado caducado (más de un TTL sin comprobar) se consulta como offline
**And** máquinas en tailnet pero fuera del segmento local se reportan por su presencia en la LAN física, no por su presencia en la VPN (FR-2)
**And** tests pytest verifican el ciclo TTL/2, la caducidad y la consulta de estado — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 1.4: API de inventario y tokens de dispositivo

As a usuario,
I want que el BE exponga el inventario por API protegida con token de dispositivo,
So that mi app pueda listar las máquinas de forma segura (y, en el futuro, controlarlas).

**Acceptance Criteria:**

**Given** el BE con inventario y tokens generados por CLI (32 B aleatorios, mostrados una sola vez, hash SHA-256 de la representación hex-lower almacenado)
**When** la app llama a `GET /api/v1/machines` con token válido
**Then** recibe el listado en JSON con el DTO completo del AD-10 (`id, name, ip, mac, hostname, status ∈ {online, offline, no_fiable}, managed`)
**And** peticiones sin token o con token inválido/revocado → 401; 5 fallos/5 min → 429 durante 15 min (FR-10, AD-6)
**And** `POST /api/v1/scan` fuerza escaneo y devuelve 202 con estadísticas si ya hay uno en curso (AD-3)
**And** el error envelope es uniforme `{"error": {"code", "message"}}` (AD-1)
**And** la API escucha solo en la interfaz tailnet y loopback (FR-9, AD-6)
**And** tests pytest del cliente de pruebas cubren auth, catálogo de errores y envelope — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 1.5: Listado de máquinas en la app (tema oscuro, poll y escanear ahora)

As a usuario,
I want ver en mi teléfono la lista de máquinas con su estado y poder forzar un escaneo,
So that tenga el inventario de mi red en el bolsillo.

**Acceptance Criteria:**

**Given** la app con token configurado apuntando al BE
**When** se abre la lista
**Then** se muestran las `Machine row` conforme UX-DR2: indicador de estado punto 10dp + texto meta (nunca solo color — UX-DR5), nombre en título, IP/MAC en meta monoespaciada, acciones inline según estado (descubierta → Dar de alta; gestionada/offline → Encender; gestionada/online → Apagar; no_fiable → badge ámbar sin acciones destructivas)
**And** el listado se refresca con polling 30 s default (10–300 s, pausable en ahorro de batería), pull-to-refresh y botón "escanear ahora" que dispara `POST /scan` y actualiza la lista (FR-12)
**And** fallos de conexión muestran la lista cacheada con badge "Sin conexión" sin romper el contenido (UX-DR4)
**And** lista vacía muestra "No se encontraron máquinas" con acción de escaneo (UX-DR4)
**And** el tema es oscuro por defecto con Material 3 + Dynamic Color y fallback a la paleta de DESIGN.md (UX-DR1)
**And** tests con Robolectric cubren: ViewModel del listado (poll, escanear ahora, caché off-line) y la fila según estados — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 1.6: Primer arranque y configuración de conexión

As a usuario,
I want configurar la conexión a mi BE (URL + token) en el primer arranque y mantener sesión,
So that no tenga que hacerlo cada vez que abro la app.

**Acceptance Criteria:**

**Given** la app recién instalada sin configurar
**When** se abre
**Then** aparece la pantalla de primer arranque (UX-DR4): explicación breve + botón "Configurar"
**And** en ajustes se editan URL y token; al guardar se valida contra `GET /status`: 401 → "Token rechazado — revisa los ajustes", timeout → "BE inalcanzable" (FR-15, UX-DR9)
**And** el token se guarda en Keystore + Encrypted DataStore, nunca en SharedPreferences (FR-15, AD-8, NFR-7)
**And** la sesión se reutiliza en cada arranque salvo 401 revocado, que redirige a ajustes (FR-15)
**And** strings ES/EN externas desde v1 (NFR-9)
**And** tests con Robolectric cubren: validación 401/timeout, almacenamiento seguro y reutilización de sesión — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

## Epic 2: Enciende y apaga — ciclo completo de control

Tras este epic, el usuario da de alta una máquina (una sola vez), la enciende por WOL y la apaga por SSH desde la app, con token por dispositivo y seguridad de fingerprint.

### Story 2.1: Alta de máquinas con password de un solo uso (asyncssh)

As a usuario,
I want dar de alta una máquina introduciendo usuario y password SSH una sola vez,
So that el BE instale su clave y pueda apagarla en el futuro sin exponer mis credenciales a nadie.

**Acceptance Criteria:**

**Given** una máquina descubierta y el BE con su par de claves Ed25519 (600, usuario no-root wakemeup) y config `[ssh]`
**When** se ejecuta el alta con password (desde la app o CLI)
**Then** el BE conecta con asyncssh en proceso (sin sshpass): la password vive solo en memoria del proceso, nunca en disco, logs, argv o entorno (FR-6, AD-5, NFR-2)
**And** captura y fija el fingerprint de host `SHA256:base64` en la máquina (AD-2, AD-9)
**And** copia la clave pública por SFTP a `~/.ssh/authorized_keys` del usuario remoto de forma idempotente (si ya está, no se duplica) (AD-5)
**And** la máquina pasa a `managed: true` y puede apagarse
**And** si la password falla: error claro ("Credenciales incorrectas"), la máquina sigue descubierta, y la password igualmente se descarta (FR-6)
**And** tests pytest: alta exitosa, password fallida, idempotencia del authorized_keys (doble de asyncssh) — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 2.2: Encendido por WOL (magic packet al broadcast local)

As a usuario,
I want encender una máquina de mi red desde la app estando fuera de casa,
So that la encuentre arrancada sin que nadie tenga que tocarla.

**Acceptance Criteria:**

**Given** una máquina gestionada offline y el BE en la LAN física
**When** se dispara el wake
**Then** el BE valida la MAC (12 hex, no multicast, distinta de cero → 422 si inválida) y emite un único magic packet al broadcast de la subred física por la interfaz Ethernet si existe (FR-4, FR-5, AD-4)
**And** si no hay interfaz Ethernet, `GET /status` reporta `warning` (WOL no fiable vía WiFi) sin bloquear el resto (FR-5)
**And** el envío es único por petición, sin retry automático, y queda en el log (FR-4)
**And** el endpoint responde éxito aunque la máquina tarde en arrancar; la confirmación real llega del estado (FR-4, FR-2)
**And** tests pytest: paquete correcto (6×0xFF + MAC ×16), MAC inválida → 422, selección de interfaz — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 2.3: Apagado por SSH con verificación de fingerprint

As a usuario,
I want apagar una máquina gestionada desde la app,
So that la apague sin levantarme y con confirmación explícita.

**Acceptance Criteria:**

**Given** una máquina gestionada online con fingerprint fijado
**When** se ejecuta el shutdown
**Then** el BE verifica que el fingerprint actual coincide con el fijado; si coincide, ejecuta el comando de apagado configurado (`sudo -n systemctl poweroff` default) vía asyncssh (FR-7, AD-2, AD-4, AD-9)
**And** si el fingerprint no coincide: NO ejecuta el apagado, marca la máquina `no_fiable` y registra el evento (AD-2)
**And** el apagado falla con error claro si el usuario remoto no tiene sudoers NOPASSWD (documentado en el alta) (FR-7)
**And** el resultado se registra en el log con canal `api`
**And** tests pytest: shutdown exitoso, fingerprint mismatch → no_fiable sin ejecución, error de sudo — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 2.4: Botones Encender / Apagar / Alta en la app (UX-DR3, UX-DR5, UX-DR6)

As a usuario,
I want encender, apagar y dar de alta máquinas con un toque desde la lista,
So that controle mi red desde el móvil sin herramientas técnicas.

**Acceptance Criteria:**

**Given** la lista de máquinas con sus estados (DTO completo AD-10)
**When** toco el botón inline de la fila
**Then** Encender (gestionada/offline) dispara wake; Apagar (gestionada/online) exige el diálogo de advertencia obligatorio "¿Apagar <máquina>? No podrás acceder a ella…" con Cancelar/Apagar — nunca un tap silencioso (FR-13, UX-DR3)
**And** en descubiertas, el botón "Dar de alta" abre el diálogo de alta (UX-DR6): Usuario + Password con toggle de visibilidad, aviso "La password se usa una sola vez y no se guarda", CTA con progreso y error inline con motivo
**And** acciones fallidas permiten reintento sin salir de la pantalla; snackbar informa resultados propios (éxito breve / error con motivo humano) (FR-13, UX-DR9)
**And** no_fiable se degrada a badge ámbar sin acciones destructivas (UX-DR5, AD-10)
**And** tests Robolectric: diálogo de apagado se muestra siempre, alta con password no persistida, reintentos — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 2.5: Gestión de tokens y registro de actividad (CLI + logs)

As a administrador,
I want generar y revocar tokens por dispositivo y ver el registro de actividad,
So that controle quién accede (desde qué dispositivo o agente) y pueda medir el uso.

**Acceptance Criteria:**

**Given** la CLI del BE
**When** se genera un token de dispositivo o de MCP
**Then** se muestra una sola vez (32 B hex) y se almacena solo su hash SHA-256 de la representación hex-lower (FR-10, FR-10b, AD-6)
**And** revocar un token no afecta a los demás; el token MCP es independiente de los de dispositivo (FR-10b)
**And** el backoff de autenticación (5 fallos/5 min → 429/15 min) aplica a la CLI y a la API; los contadores viven en memoria y se documenta su reset al reiniciar (AD-6)
**And** los logs de actividad registran timestamp, canal `api|mcp`, token, máquina y resultado; nunca passwords (FR-11)
**And** los logs permiten computar SM-1..SM-4 sin atribución personal (FR-11)
**And** tests pytest: ciclo de vida de tokens, revocación, backoff, shape de logs — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

## Epic 3: Agente IA en la red — MCP y eventos en vivo

Tras este epic, un agente IA conectado desde la tailnet consulta y controla las máquinas vía MCP, y cualquier cambio de estado se refleja al instante en la app.

### Story 3.1: Servidor MCP con tools de lectura y control

As a un agente IA (configurado por su operador),
I want consultar y controlar las máquinas de la red vía MCP,
So that pueda ayudar a encender/apagar equipos sin interfaces manuales.

**Acceptance Criteria:**

**Given** el BE con FastAPI y el SDK oficial `mcp` (2.2.x) montado (`mcp.streamable_http_app()` bajo `/api/v1/mcp` con `transport_security`)
**When** el agente invoca las tools
**Then** dispone de: `list_machines` (inventario con estado y estatus), `get_machine_status`, `wake_machine`, `shutdown_machine`, `force_scan` (FR-16)
**And** NO existe ninguna tool de alta ni de gestión de claves: el agente no puede instalar claves ni tratar passwords SSH (FR-16, AD-12)
**And** las tools de control reutilizan los mismos servicios y verificaciones que la API: MAC válida, fingerprint fijado, comando restringido (AD-12, AD-2, AD-4)
**And** las acciones de las tools emiten eventos al bus (AD-11) y se registran con canal `mcp` (FR-11)
**And** tests pytest: descubrimiento del contrato (tools listadas, sin herramientas de alta), wake/shutdown vía tool con y sin permisos — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 3.2: Autenticación y alcance del MCP (token dedicado, solo tailnet)

As a administrador,
I want que el MCP exija su propio token y solo sea accesible desde la tailnet,
So that un agente comprometido no afecte a los móviles y el agent no quede expuesto fuera de la VPN.

**Acceptance Criteria:**

**Given** la CLI del BE con generación de token MCP (FR-10b)
**When** un cliente MCP se conecta
**Then** sin token de MCP o con token inválido se rechaza la sesión (401/error de protocolo) (FR-17)
**And** el servidor MCP escucha solo en la interfaz tailnet + loopback; no es accesible desde la LAN física ni desde fuera de la VPN (AD-12, NFR-1)
**And** revocar el token MCP no afecta a los tokens de dispositivo (FR-10b)
**And** los eventos de autenticación fallida del MCP quedan en el registro (FR-11)
**And** tests pytest: conexión sin token, token inválido, indisponibilidad fuera de la interfaz tailnet (bind check) — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 3.3: Bus de eventos + stream SSE en el BE (AD-11)

As a app Android,
I want recibir cada cambio de estado en tiempo real,
So que la lista refleje al instante lo que ocurre (venga de la app, del agente o del escaneo).

**Acceptance Criteria:**

**Given** el bus interno asyncio pub/sub del BE (AD-11)
**When** ocurre un cambio de estado (online/offline/no_fiable, alta completada, escaneo terminado) desde cualquier origen (API, MCP, escaneo periódico)
**Then** se persiste en el registro (FR-11) y se emite un evento SSE en JSON (tipo, máquina, timestamp) a los suscriptores conectados (FR-18)
**And** el stream vive en `/api/v1/events` con autenticación Bearer por token de dispositivo y solo en la interfaz tailnet (AD-11, AD-6)
**And** el evento nunca transporta credenciales ni claves (FR-11)
**And** si no hay suscriptores, el evento se omite (no hay cola de entrega en v1)
**And** tests pytest: publicación al bus → eventos emitidos y registrados; stream desconectado no rompe el servicio — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 3.4: Suscripción SSE en la app con fallback de polling

As a usuario,
I want que la lista se actualice sola cuando algo cambia,
So que el estado que veo sea el real, aunque el cambio lo haya hecho otro (app, agente o escaneo).

**Acceptance Criteria:**

**Given** la app con la lista abierta y token de dispositivo válido
**When** llega un evento del stream (p. ej. el agente apaga una máquina)
**Then** la fila afectada se actualiza sin spinner ni snackbar (las acciones de otros no muestran snackbar; solo las propias) (FR-18, UX-DR7, Flow 4)
**And** con stream activo, el estado se refleja en ≤2 s; con stream caído o ahorro de batería, el polling 30 s (10–300 s) cubre el hueco (FR-18, FR-12)
**And** el stream se reconecta con backoff exponencial si se interrumpe, sin avisar al usuario; si se pausa por batería, el polling queda activo (FR-18, NFR-10)
**And** el DTO del evento se deserializa robustamente: evento mal formado se ignora sin crash (AD-10, AD-11)
**And** tests Robolectric: actualización de fila por evento, fallback de polling, reconexión con backoff — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

## Epic 4: Lanzamiento pulido — distribuir la herramienta con confianza

Tras este epic, la app se distribuye públicamente: primer arranque guiado, estados de error claros, accesibilidad completa, bilingüe y despliegue operacional del BE robusto.

### Story 4.1: Despliegue operacional completo del BE

As a administrador,
I want instalar y actualizar el BE de forma idempotente como servicio systemd,
So que sobreviva a reinicios y se monte sin pasos manuales frágiles.

**Acceptance Criteria:**

**Given** Raspberry Pi OS Bookworm con tailnet activa
**When** se ejecuta el instalador
**Then** crea el usuario dedicado `wakemeup`, genera el par Ed25519 si no existe (600/700), escribe la config desde ejemplo y despliega la unidad systemd (`Restart=always`, healthcheck vía `GET /status` 200 = vivo)
**And** re-ejecutar el instalador no duplica claves ni rompe el servicio (idempotente)
**And** el servicio usa el Python gestionado por `uv` (ruta explícita), no el Python de sistema 3.11 (AD-7, Entorno operacional)
**And** logs a journald; nunca passwords en los logs (FR-11)
**And** el backend arranca correctamente aunque la interfaz tailnet aparezca más tarde que el servicio (bind post-start / retry documentado)
**And** tests: instalador idempotente (pytest) + checklist manual — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 4.2: Onboarding completo y estados de error (UX-DR4, UX-DR9)

As a usuario recién llegado,
I want entender qué es la app y qué hacer si algo falla,
So que el valor quede claro sin manual y los errores no me bloqueen.

**Acceptance Criteria:**

**Given** una instalación nueva
**When** abro la app por primera vez
**Then** veo la pantalla de primer arranque (UX-DR4): breve explicación sin tecnicismos + botón "Configurar" que me lleva a ajustes
**And** con token inválido/revocado veo banner persistente "Token rechazado — revisa los ajustes" y el botón de ajustes queda accesible (UX-DR4)
**And** con el BE inalcanzable veo la lista cacheada con badge "Sin conexión" y el contenido no se rompe (UX-DR4)
**And** los mensajes de snackbar y diálogos usan el Voice de EXPERIENCE.md sin jerga ("Despierta tu máquina desde cualquier lugar", "Máquina no responde: ¿está encendida y con SSH activo?") (UX-DR9)
**And** todas las strings están externalizadas ES/EN (NFR-9)
**And** tests Robolectric: flujo de onboarding, banner 401, estado sin conexión — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 4.3: Accesibilidad completa (UX-DR8)

As a todos los usuarios (incluidos los que usan TalkBack o ajustes de texto grandes),
I want una app accesible de serie,
So que nadie se quede fuera al distribuirse la herramienta.

**Acceptance Criteria:**

**Given** la app con estados de fila e interacciones
**When** se recorre con TalkBack
**Then** cada fila anuncia nombre, estado y acciones disponibles ("Desktop, encendida. Botón Apagar."); el diálogo de apagado anuncia título y acciones al abrir (UX-DR8)
**And** los estados nunca se comunican solo por color: punto + texto en cada fila (AD-10, NFR-8)
**And** los targets táctiles miden ≥48dp; con Dynamic Type al máximo la fila mantiene legibilidad sin truncar controles (UX-DR8)
**And** con Reduce Motion activo, el icono de escaneo no gira: se sustituye por texto "Escaneando…" y los diálogos no transicionan (UX-DR8)
**And** tests Robolectric (semántica de contenido, tamaños de target) — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)

### Story 3.5: Notificación del sistema para cambios del MCP

As a usuario,
I want una notificación del sistema cada vez que el agente IA apague o encienda una máquina,
So que sepa quién la cambió aunque no esté mirando la app.

**Acceptance Criteria:**

**Given** la app con el permiso de notificaciones concedido y suscripción SSE activa
**When** llega un evento del stream con origen `mcp` (el agente IA apaga/enciende una máquina o force_scan cambia un estado)
**Then** la app muestra una notificación del sistema (bandeja, con la app abierta o en segundo plano): "«máquina» — El agente IA <acción>" (FR-18)
**And** la fila de la máquina se actualiza igualmente (reflejo SSE, con o sin notificación)
**And** los eventos de otros orígenes (otro dispositivo, escaneo periódico) NO generan notificación, solo actualizan la fila (FR-18)
**And** si el permiso `POST_NOTIFICATIONS` está denegado: no hay notificación pero el estado se refleja; la app sugiere activarlo desde ajustes (FR-18)
**And** tests Robolectric: evento con origen `mcp` → notificación emitida al gestor de notificaciones; origen `api`/`scan` → sin notificación — requisito transversal de tests
**And** commit + push al finalizar (requisito transversal)
