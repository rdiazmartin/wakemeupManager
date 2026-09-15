# Epic 4 Context: Lanzamiento pulido — distribuir la herramienta con confianza

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Este epic convierte wakemeupManager en una herramienta pública y distribuible con confianza. Cierra el ciclo de vida del producto: el BE se instala y actualiza de forma idempotente como servicio systemd que sobrevive a reinicios y arranca aunque la tailnet aparezca más tarde; la app ofrece un primer arranque guiado, estados de error claros (token rechazado, BE inalcanzable, sin conexión), accesibilidad completa para TalkBack y Dynamic Type, y todos los textos bilingües ES/EN; y los cambios de estado originados por el agente IA se notifican al usuario aunque no esté mirando la app. Importa porque hasta aquí la herramienta funcionaba, pero no estaba lista para que un tercero la instalara y la usara sin fricción ni soporte técnico.

## Stories

- Story 4.1: Despliegue operacional completo del BE
- Story 4.2: Onboarding completo y estados de error
- Story 4.3: Accesibilidad completa
- Story 3.5: Notificación del sistema para cambios del MCP

## Requirements & Constraints

- **Primer arranque y errores (UX-DR4, UX-DR9):** instalación nueva → pantalla dedicada con explicación sin tecnicismos y botón "Configurar" (sin lista fantasma); token inválido/revocado → banner persistente "Token rechazado — revisa los ajustes" con ajustes accesibles; BE inalcanzable → lista cacheada con badge "Sin conexión" sin romper el contenido; lista vacía → mensaje con acción de escaneo.
- **Copys con el Voice del producto:** mensajes humanos, sin jerga de red ("Despierta tu máquina desde cualquier lugar", "Máquina no responde: ¿está encendida y con SSH activo?", "La password se usa una sola vez y no se guarda"). Nunca exponer protocolos ni códigos HTTP al usuario.
- **i18n (NFR-9):** todas las strings externalizadas ES/EN desde v1; incluye snackbars, diálogos, notificaciones y pantallas de error.
- **Accesibilidad (NFR-8, UX-DR8):** los estados se comunican siempre con punto + texto, nunca solo color; TalkBack anuncia cada fila con nombre, estado y acciones disponibles y el diálogo de apagado anuncia título y acciones al abrir; targets táctiles ≥48dp; con Dynamic Type al máximo la fila mantiene legibilidad sin truncar controles; con Reduce Motion el icono de escaneo no gira y se sustituye por texto "Escaneando…".
- **Batería (NFR-10):** el polling y el stream SSE se pausan en modo ahorro de batería; el polling cubre el hueco.
- **Almacenamiento seguro del token (NFR-7):** Keystore Android + Encrypted DataStore; nunca SharedPreferences.
- **Notificación del agente:** solo los eventos con origen `mcp` generan notificación del sistema (bandeja, con la app abierta o en segundo plano), con canal propio y permiso `POST_NOTIFICATIONS` pedido con explicación; si está denegado, la fila igualmente se actualiza y la app sugiere activarlo. Los orígenes `api`/`scan`/`periodic` nunca notifican.
- **Despliegue operacional:** instalador idempotente que crea el usuario dedicado, genera el par Ed25519 si no existe, escribe la config desde ejemplo y despliega la unidad systemd; re-ejecutarlo no duplica claves ni rompe el servicio; healthcheck vía `GET /status` (200 = vivo); logs a journald, nunca passwords.
- **Proceso (transversal):** todo el código cubierto por tests (pytest en BE, JVM + Robolectric en app) y commit + push obligatorio al final de cada story.

## Technical Decisions

- **Unidad systemd de sistema** con `Wants=network-online.target`, `Restart=always`, `RestartSec=5`, usuario dedicado `wakemeup`. Directorios: `/etc/wakemeup/config.toml` y `/var/lib/wakemeup/` (SQLite + claves con 600/700).
- **Python gestionado por `uv`** (ruta explícita en la unidad); nunca el Python de sistema 3.11 de Raspberry Pi OS Bookworm (arm64).
- **Arranque tolerante a la tailnet tardía:** la interfaz tailnet puede aparecer después que el servicio; el BE debe reintentar el bind (documentado) en lugar de fallar.
- **Un solo entorno** (Raspberry); local dev con `uv run uvicorn` escuchando solo en loopback. Config en `config.toml` con override por env, secciones `[scan] [api] [ssh] [auth]`.
- **App Android:** MVVM + UDF (ViewModel + StateFlow + `collectAsStateWithLifecycle`), UI sin lógica de negocio. Contrato único con el BE: la API REST (`/api/v1`); la app no lee SQLite ni comparte código.
- **SSE:** el stream vive en `/api/v1/events` con auth Bearer por token de dispositivo; evento con origen `mcp` es la fuente de verdad de la notificación. El evento nunca transporta credenciales ni claves.
- **Tema oscuro por defecto** con Material 3 + Dynamic Color (Material You); fallback a la paleta grafito/acento verde eléctrico.
- **Catálogo de errores** con cuerpo uniforme `{"error": {"code", "message"}}`.

## UX & Interaction Patterns

- **Pantalla de primer arranque:** superficie dedicada, explicación breve + "Configurar" hacia ajustes; sin lista fantasma.
- **Banner 401:** persistente, no un toast efímero; el acceso a ajustes queda siempre disponible.
- **Sin conexión:** se mantiene la lista previa cacheada con badge "Sin conexión"; nunca romper el contenido.
- **Notificación de agente:** título = máquina, cuerpo = acción del agente; solo para origen `mcp`. La fila se actualiza igualmente con o sin notificación.
- **Accesibilidad de fila y diálogo:** semántica de contenido con rol + estado; diálogo modal de apagado anunciado; orden de foco coherente.
- **Reduce Motion:** sin transiciones en diálogos y sin rotación del loader de escaneo (texto en su lugar).
- **Microcopy:** voz técnica pero amable, bilingüe; los detalles técnicos (IP/MAC) van en monoespaciada sin itálicas.

## Cross-Story Dependencies

- **Story 3.5 depende de Epic 3:** requiere el bus de eventos, el stream SSE (`/api/v1/events`) y el origen `mcp` etiquetado en los eventos; la infraestructura de notificaciones y el permiso `POST_NOTIFICATIONS` son nuevos en este epic.
- **Story 4.1 depende del esqueleto del BE (Story 1.1):** la unidad systemd, la config de ejemplo y el healthcheck `/status` ya existen; aquí se completa el instalador idempotente.
- **Story 4.2 reutiliza el flujo de configuración/sesión (Story 1.6)** y los estados de fila definidos en Epics 1–2.
- **Story 4.3 aplica a componentes de fila y diálogos** ya construidos en Epics 1–2 (no introduce pantallas nuevas).
- **El requisito transversal de tests + commit/push aplica a todas las stories**, incluidas las de este epic.
