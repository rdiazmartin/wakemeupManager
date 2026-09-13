# Epic 1 Context: Descubre tu red

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Tras este epic, el usuario abre la app Android y ve todas las máquinas de su red con su estado online/offline — actualizado solo por el backend — pudiendo forzar un escaneo desde el móvil. Cubre la base del sistema: esqueleto hexagonal del BE, descubrimiento de máquinas por LAN física, mantenimiento de estado con TTL, la API de inventario autenticada por token de dispositivo y el listado+configuración de conexión en la app. Es el fundamento sobre el que los epics 2 (wake/shutdown/alta) y 3 (MCP/SSE) construyen. FRs cubiertos: FR-1, FR-2, FR-3, FR-5 (parcial: reporte de interfaz WOL), FR-12.

## Stories

- Story 1.1: Esqueleto del BE — hexagonal, config y despliegue base
- Story 1.2: Descubrimiento de máquinas (escaneo del rango)
- Story 1.3: Estado online/offline con TTL
- Story 1.4: API de inventario y tokens de dispositivo
- Story 1.5: Listado de máquinas en la app (tema oscuro, poll y escanear ahora)
- Story 1.6: Primer arranque y configuración de conexión

## Requirements & Constraints

- **Descubrimiento sin root**: barrido ICMP (ping binario) + lectura de `/proc/net/arp`; registra IP, MAC y hostname (si resuelve) con upsert. Excluye las interfaces propias del BE (incluida tailnet y loopback) y direcciones no-LAN.
- **Serializado y no destructivo**: un solo escaneo a la vez; escaneos concurrentes esperan (singleflight) sin corromper el inventario. Máquinas ausentes → `offline`, nunca borradas; el borrado solo existe vía `DELETE` explícito (uso avanzado, fuera de la UI de v1).
- **Estado con TTL**: comprobación cada TTL/2; TTL default 60 s configurable 15–300 s; estado caducado se consulta como offline. El estado refleja presencia en la LAN física, no en la VPN.
- **Escaneo periódico y forzado**: redisparo automático cada 10 min (configurable) + `POST /scan` forzado que responde 202 con estadísticas si ya hay uno en curso (nunca 409).
- **API autenticada, solo tailnet + loopback**: rutas `/api/v1` con prefijo `/api/v1/status` (estado/versión, healthcheck) y `/api/v1/machines` (listado con DTO completo: `id, name, ip, mac, hostname, status ∈ {online, offline, no_fiable}, managed`). Sin token o token inválido/revocado → 401; 5 fallos/5 min → 429 durante 15 min (contadores en memoria, se resetean al reiniciar). Error envelope uniforme `{"error": {"code", "message"}}`.
- **Tokens de dispositivo**: 32 B aleatorios generados por la CLI del BE, mostrados una sola vez; se almacena solo SHA-256 de la representación hex-lower.
- **Reporte WOL (parcial)**: `GET /status` expone la interfaz emisora WOL; si no hay interfaz Ethernet, reporta `warning` (WOL no fiable vía WiFi) sin bloquear el resto. El envío del magic packet es del Epic 2.
- **Calidad transversal (obligatorio)**: toda story con tests pytest (servicios, adaptadores, API con cliente de pruebas) y commit + push al finalizar cada story. Stack fijado: Python 3.14 vía uv, FastAPI 0.141, aiosqlite 0.22; Raspberry Pi OS Bookworm arm64; paradigma hexagonal.

## Technical Decisions

- **Hexagonal BE**: paquetes `src/wakemeup/{api, services, adapters, core, cli}`, dependencias solo hacia abajo (adaptadores → servicios → núcleo); núcleo sin IO. Config en `config.toml` + override por env, secciones `[scan] [api] [ssh] [auth]`.
- **Persistencia SQLite** (aiosqlite) para inventario, tokens y registro; claves y config fuera de la DB con permisos 600/700. Fechas ISO-8601 UTC. Naming snake_case, rutas kebab-case plural.
- **Identidad de máquina**: id entero PK; la IP es efímera (atributo de estado, no de identidad); fingerprint `SHA256:<base64>` fijado en el alta (Epic 2) pero ya modelado en la entidad.
- **Despliegue base**: unidad systemd con usuario dedicado `wakemeup`, `Restart=always`, `Wants=network-online.target`, Python de uv explícito (no el de sistema 3.11); `GET /status` 200 = healthcheck. Dev local con `uv run uvicorn` en loopback.
- **App MVVM + UDF**: ViewModel + StateFlow + `collectAsStateWithLifecycle`; sin lógica de negocio en UI. Ktor client 3.5.2, Kotlin 2.4.20, Compose BOM 2026.09.00 (M3 1.4.0), AGP 9.4/Gradle 9.7, targetSdk 36 / minSdk 26. Strings ES/EN externalizadas desde v1 (res/values + res/values-en). Paquetes por feature: data/remote, data/local, domain, ui.

## UX & Interaction Patterns

- **Tema oscuro por defecto** con Material 3 + Dynamic Color; fallback a paleta grafito (`surface-base #121417`, `surface-raised #1A1D21`, `accent #2DE0A5`, success/warning/danger). Dark-first; la lista y sus botones son los protagonistas, sin brillos.
- **Machine row**: indicador de estado punto 10dp + texto meta corto (nunca solo color), nombre en título, IP/MAC en meta monoespaciada, acciones inline a la derecha con icono+texto según estado: descubierta → "Dar de alta"; gestionada/offline → "Encender"; gestionada/online → "Apagar"; no_fiable → badge ámbar sin acciones destructivas. Fila offline con menos contraste. Margen 16dp, filas ≥72dp, modales de un solo nivel.
- **Listado**: polling 30 s default (10–300 s, pausable en ahorro de batería), pull-to-refresh, botón "escanear ahora" en header (gira durante el escaneo; con Reduce Motion → texto "Escaneando…"). Fallos de conexión: lista cacheada con badge "Sin conexión", sin romper el contenido. Lista vacía: "No se encontraron máquinas..." + acción de escaneo. Skeleton de 4–6 filas en el primer fetch.
- **Primer arranque**: pantalla dedicada con explicación breve sin tecnicismos + botón "Configurar". Config de URL + token validada contra `GET /status`: 401 → "Token rechazado — revisa los ajustes", timeout → "BE inalcanzable". Token en Keystore + Encrypted DataStore (EncryptedSharedPreferences excluida por deprecación); sesión reutilizada en cada arranque salvo 401 revocado → redirige a ajustes. Banner persistente de token inválido con configuración accesible.
- **Accesibilidad**: estados punto+texto siempre; targets ≥48dp; Dynamic Type sin truncar controles. Microcopy bilingüe en voz de casa, sin jerga de red.

## Cross-Story Dependencies

- **1.1 es base de 1.2–1.4**: el esqueleto, config y healthcheck habilitan discovery, estado, API y tokens.
- **1.2 alimenta 1.3**: el descubrimiento produce el inventario sobre el que el estado (TTL/2) opera; ambos son prerequisito de 1.4 (el listado de la API expone el estado).
- **1.4 es consumido por la app**: 1.5 (listado/poll/scan) depende del contrato `GET /machines` + DTO completo y de `POST /scan`; 1.6 (config) depende de `GET /status` y del mismo mecanismo de tokens.
- **Hacia Epic 2**: el estado no_fiable y el reporte de interfaz WOL en `GET /status` (1.4) sustentan wake/shutdown; el DTO de máquina fijado en 1.4 es el wire model que Epic 2 extiende con acciones.
- **Hacia Epic 3**: el escaneo terminado y los cambios de estado de este epic serán fuentes de eventos del bus SSE.
