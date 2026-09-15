# Epic 3 Context: Agente IA en la red — MCP y eventos en vivo

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Tras este epic, un agente IA conectado desde dentro de la tailnet puede consultar y controlar las máquinas de la red vía MCP con su propio token dedicado, sin acceso al alta ni a credenciales SSH. Además, cualquier cambio de estado —lo origine el agente, la app o el escaneo— se propaga al instante a la app Android mediante un bus interno y un stream SSE sobre tailnet, de modo que la lista refleja la realidad sin esperar al polling; el polling de 30 s queda como fallback y los cambios originados por el MCP disparan una notificación del sistema.

## Stories

- Story 3.1: Servidor MCP con tools de lectura y control (`list_machines`, `get_machine_status`, `wake_machine`, `shutdown_machine`, `force_scan`; sin tools de alta ni gestión de claves).
- Story 3.2: Autenticación y alcance del MCP — token dedicado, solo tailnet + loopback.
- Story 3.3: Bus de eventos + stream SSE en el BE (asyncio pub/sub, `/api/v1/events`).
- Story 3.4: Suscripción SSE en la app con fallback de polling y reconexión con backoff.
- Story 3.5: Notificación del sistema para cambios de estado originados por el MCP.

## Requirements & Constraints

- Servidor MCP con el SDK oficial `mcp` (2.2.x), montado como `mcp.streamable_http_app()` bajo `/api/v1/mcp` sobre la app FastAPI del BE (con configuración de transport security). Contrato MCP estándar (JSON-RPC sobre el transporte de la VPN).
- Tools de v1 exactamente: `list_machines` (inventario con estado y estatus), `get_machine_status`, `wake_machine`, `shutdown_machine`, `force_scan`. No se expone ninguna tool de alta ni de gestión de claves: el agente nunca trata passwords SSH.
- Las tools de control reutilizan los mismos servicios y verificaciones que la API (MAC válida, fingerprint fijado, comando restringido); no existe implementación paralela.
- Auth: token de MCP independiente de los tokens de dispositivo (mismo formato 32 B hex, mostrado una sola vez, almacenado solo como hash SHA-256 de la representación hex-lower, revocable por CLI). Sin token o token inválido → sesión rechazada (401 / error de protocolo). Revocar el token MCP no afecta a los móviles y viceversa.
- El servidor MCP escucha solo en la interfaz tailnet + loopback: inaccesible desde la LAN física o fuera de la VPN (NFR-1). Los fallos de autenticación del MCP quedan en el registro.
- El bus de eventos cubre todo cambio de estado (online/offline/no_fiable, alta completada, escaneo terminado) desde cualquier origen (API, MCP, escaneo periódico): persiste en el registro de actividad y emite un evento SSE en JSON con tipo, máquina, timestamp y origen ∈ {api, mcp, scan, periodic}.
- El stream vive en `/api/v1/events` (AD-1), autenticado con Bearer de token de dispositivo y solo sobre la interfaz tailnet. El evento nunca transporta credenciales ni claves. Sin suscriptores, el evento se omite (v1 no tiene cola de entrega).
- Con stream activo el estado se refleja en ≤2 s; con stream caído o ahorro de batería, el polling de 30 s (10–300 s) cubre el hueco. El stream se pausa bajo ahorro de batería y se reconecta con backoff exponencial sin avisar al usuario.
- Las acciones del MCP se registran con canal `mcp` (frente a `api` de la app), lo que permite computar las métricas de apagados vía API y agente sin atribución personal.
- La notificación del sistema requiere permiso `POST_NOTIFICATIONS` (pedido con explicación); si está denegado, no hay notificación pero la fila se actualiza igual y la app sugiere activarlo desde ajustes.
- NFR transversal de proceso: cada story requiere tests automatizados de sus criterios de aceptación (pytest en el BE; Robolectric/tests JVM en la app) y commit + push obligatorio al finalizar.

## Technical Decisions

- Arquitectura hexagonal del BE: el MCP y el stream viven en la capa API (`api/mcp*`, `api/events`) y llaman a los servicios existentes; el bus interno es un pub/sub asyncio. El MCP valida su token en la capa FastAPI antes de montarlo.
- Estructura interna del BE: `src/wakemeup/{api,services,adapters,core,cli}`; el API/MCP no ejecuta acciones directamente (AD-4). Persistencia en SQLite vía `aiosqlite` (inventario, hashes de tokens, fingerprints, registro de actividad).
- Token MCP: mismo mecanismo que AD-6 (pre-imagen canónica hex-lower; `SHA256(hex-lower)` en SQLite); la CLI genera y revoca. El backoff de autenticación aplica también al MCP.
- App Android MVVM + UDF (ViewModel + StateFlow, `collectAsStateWithLifecycle`). Cliente Ktor 3.5.2 con el plugin SSE incluido en `ktor-client-core`; DTOs del evento deserializados robustamente: un evento mal formado se ignora sin crash. Token en Keystore + Encrypted DataStore.
- El DTO de máquina en el wire incluye `status ∈ {online, offline, no_fiable}` y `managed ∈ {true, false}`; la app nunca deduce `no_fiable`. El origen del evento es el discriminante para la notificación.
- Superficie de rutas verbatim (prefijo `/api/v1`): `GET /events` (SSE, Bearer) y `GET /mcp` (SDK MCP montado, token MCP). Errores con envelope uniforme `{"error": {"code", "message"}}`.
- Stack BE: Python 3.14 vía `uv`, FastAPI + uvicorn 0.141.x, SDK `mcp` 2.2.x. App: Kotlin 2.4.20, Compose BOM 2026.09.00, targetSdk 36/minSdk 26.

## UX & Interaction Patterns

- Eventos SSE: la fila afectada se actualiza sin spinner ni snackbar para acciones ajenas; la app solo muestra snackbar para las acciones que dispara el propio usuario. Sin indicador visual de "conexión en vivo" en v1: el estado de la fila es la única señal.
- Si el stream cae, el polling de 30 s cubre el hueco sin avisar al usuario; si se pausa por batería, el polling queda activo.
- Notificación de agente (bandeja del sistema), visible con la app abierta o en segundo plano: título = máquina, cuerpo = acción ("NAS — El agente apagó la máquina"), con channel propio. Solo los eventos con origen `mcp` notifican; los de otro dispositivo o del escaneo únicamente actualizan la fila.
- El permiso `POST_NOTIFICATIONS` se pide con explicación (onboarding o al primer evento); denegado → sin notificación, la fila se refleja igualmente y se sugiere activarlo desde ajustes.
- Comportamiento de referencia: Flow 4 — el agente IA apaga la NAS y Roberto recibe la notificación aunque no esté mirando la app. Fallo: stream caído → la actualización y la notificación llegan con el polling (≤30 s).

## Cross-Story Dependencies

- 3.1 depende de los servicios de control (wake/shutdown) y de la identidad de máquina (fingerprint, MAC validada) de Epic 2, y de la superficie FastAPI de Epic 1–2.
- 3.2 depende de la CLI de tokens (FR-10b, Story 2.5) y de la infraestructura de auth de la API (Story 1.4); su bind solo-tailnet reutiliza el patrón de NFR-1/AD-6.
- 3.3 es la base de 3.4 y 3.5: el bus publica eventos desde API, MCP y escaneo; requiere el registro de actividad de Story 2.5.
- 3.1 emite eventos al bus con origen `mcp` (AD-11), lo que 3.5 consume para notificar; 3.4 necesita el stream de 3.3 y el contrato de eventos del DTO.
- 3.4 y 3.5 comparten la suscripción SSE y el fallback de polling; la notificación (3.5) se dispara en la capa que recibe los eventos, no en la UI.
