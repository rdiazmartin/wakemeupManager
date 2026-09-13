---
name: wakemeupManager
status: draft
sources:
  - {planning_artifacts}/prds/prd-wakemeupManager-2026-09-13/prd.md
updated: 2026-09-13
---

# wakemeupManager — Experience Spine

## Foundation

Aplicación Android móvil (nativa, Kotlin + Jetpack Compose, Material 3), una sola superficie principal (lista de máquinas) más modales y ajustes. Producto público y gratuito: cada instalación se conecta al BE del usuario. El tema es oscuro por defecto y sigue la preferencia del sistema (Material You dinámico cuando esté disponible). `DESIGN.md` es la referencia visual; este spine es el comportamiento.

## Information Architecture

| Surface | Reached from | Purpose |
|---|---|---|
| Lista de máquinas | App open (cold) | Estado y acciones de todas las máquinas: encender/apagar, alta |
| Alta (dialog) | Lista → máquina descubierta | Credenciales SSH de un solo uso para gestionar la máquina |
| Configuración | Lista → header (engine) | URL del BE + token de dispositivo, idioma, pruebas de conexión |
| (Sin pantalla de detalle) | — | `[ASSUMPTION: toda la interacción vive en la lista — ver FR-12/13 y decisión de sesión: acciones inline]` |

Sin tab bar, sin drawer: una lista, un botón de ajustes, modales ligeros. Todos los modales son de un solo nivel.

## Voice and Tone

Microcopy. Voz técnica pero amable; los tecnicismos de red se explican en lenguaje de casa. Bilingüe ES/EN con strings externas desde v1.

| Do | Don't |
|---|---|
| "Despierta tu máquina desde cualquier lugar." | "WOL magic packet delivery status" |
| "¿Apagar <máquina>? No podrás acceder a ella hasta que alguien la encienda." | "Are you sure you want to shut down? (Y/N)" |
| "La password se usa una sola vez y no se guarda." | "Insert SSH credentials" |
| "Máquina no responde: ¿está encendida y con SSH activo?" | "Connection refused" |
| "Token rechazado — revisa los ajustes." | "401 Unauthorized" |

## Component Patterns

## Component Patterns

| Component | Use | Behavioral rules |
|---|---|---|
| Machine row | Lista | Tap en fila: nada (sin detalle en v1) — las acciones son los botones inline. `[ASSUMPTION: sin pantalla de detalle; decidido en sesión: todo en la lista]` |
| Power button (inline) | Fila de máquina | Visible en descubiertas como CTA `Dar de alta`; en gestionadas: `Encender` (disponible si offline) y `Apagar` (disponible siempre que esté online). |
| Power off dialog | Fila → Apagar | Siempre, sin excepción; confirmación explícita antes de disparar apagado. |
| Scan button | Header | Gira (loader) durante el escaneo; al terminar, snackbar "Escaneo completado" o "Error de red". |
| Snackbar | Resultados | Éxito breve; errores con motivo humano. Solo acciones propias. |
| Notificación de agente (sistema) | Evento SSE origen `mcp` → bandeja | Cambio de estado hecho por el agente IA: notificación del sistema (título = máquina, cuerpo = acción resultado; p. ej. "NAS — El agente apagó la máquina"). Permiso `POST_NOTIFICATIONS` pedido con explicación (onboarding o al primer evento); denegado → sin notificación, el cambio se refleja igual en la lista. (FR-18) |
| Eventos en vivo (SSE) | Fila / lista | Los cambios de estado que llegan por el stream (acciones de otros dispositivos, del agente IA o del escaneo) actualizan la fila afectada sin spinner ni snackbar; la app solo muestra snackbar para las acciones que el propio usuario disparó. Si el stream cae, el polling de 30 s (FR-12) cubre el hueco sin avisar al usuario. `[ASSUMPTION: sin indicador visual de 'conexión en vivo' en v1 — el estado de la fila es la única señal]` |

## State Patterns

| State | Surface | Treatment |
|---|---|---|
| Primer arranque (sin configurar) | Toda la app | Pantalla dedicada: explicación breve + botón "Configurar" → pantalla de ajustes. Sin lista fantasma. |
| Token inválido / 401 | Lista | Banner o snackbar persistente: "Token rechazado — revisa ajustes"; el botón de configuración queda accesible. |
| Cargando (primer fetch) | Lista | Skeleton de 4-6 filas; nunca blanco durante una acción destructiva. |
| Sin conexión con el BE | Lista | Lista previa cacheadada visible con badge "Sin conexión"; no romper el contenido. |
| Lista vacía (sin máquinas) | Lista | "No se encontraron máquinas. Toca el icono de escaneo para buscar de nuevo." |
| Máquina no fiable (fingerprint mismatch) | Fila | Badge ámbar "No fiable" + no se ofrece Apagar; `[ASSUMPTION: el alta fallida/no fiable redirige a reintentar alta]` |
| Alta en curso | Dialog | Spinner en CTA; si falla: error inline con motivo y campos intactos (password reintroducir). |
| Apagado en curso | Botón Apagar | Loader en el botón; al terminar, snackbar con resultado. |
| Encendido enviado | Botón Encender | Snackbar "Encendido enviado"; el estado se actualiza en el siguiente polling (30s o refresco manual). |

## Interaction Primitives

- Tap para actuar (nunca gestos secretos para acciones destructivas).
- Pull-to-refresh en la lista (dispara re-fetch de inventario y estado).
- El popup de apagado es modal de confirmación estándar (dialog) — no bottomsheet, no haptic-only.
- El botón de escaneo del header fuerza un escaneo del BE (FR-3) de forma síncrona con loader.
- Banned: swipe-to-delete sobre máquinas (el borrado no existe en la UI de v1; `DELETE` es uso avanzado vía API/CLI), carruseles, animaciones hero de apertura, badges numéricos. Notificaciones del sistema: SOLO para cambios de estado originados por el agente IA (MCP), con permiso explícito y explicado; nunca para el resto de orígenes.

## Accessibility Floor

Comportamental; el contraste vive en `DESIGN.md`.

- TalkBack: toda fila anuncia nombre, estado y acciones disponibles ("Desktop, encendida. Botón Apagar."). El diálogo de apagado anuncia su título y sus acciones al abrir.
- Estados nunca solo por color: punto + texto (`meta`) en cada fila.
- Targets táctiles ≥48dp en altura y anchura.
- Dynamic Type: a escala máxima la fila mantiene legibilidad; los botones inline no se truncan.
- Reduce Motion: elimina rotación del icono de escaneo (se sustituye por texto "Escaneando…"); los diálogos aparecen sin transición.

## Key Flows

### Flow 1 — Encender desde fuera (Roberto, de viaje)

1. Roberto abre la app (token guardado, FR-15 reutiliza sesión).
2. La lista carga el inventario; Desktop aparece "Apagada".
3. Toca `Encender` en la fila.
4. Snackbar: "Encendido enviado".
5. En ≤60s (o tras pull-to-refresh) el indicador pasa a verde "Encendida".
6. **Climax:** la máquina que estaba a kilómetros está arrancando — el control remoto funciona.

Fallo: sin respuesta del BE → snackbar "Máquina no responde: ¿está encendida y con SSH activo?" y la fila conserva su botón para reintentar.

### Flow 2 — Alta de una máquina (María, sin contexto técnico)

1. María abre la app; la lista muestra el portátil como "Descubierta".
2. Toca `Dar de alta`.
3. Dialog pide Usuario y Password; el aviso dice "La password se usa una sola vez y no se guarda".
4. Confirma; el CTA muestra progreso.
5. La fila pasa a estado "Encendida/Apagada" con botones activos.
6. **Climax:** María no ha visto jamás una clave SSH y ya controla la máquina.

Fallo: password incorrecta → error inline "Credenciales incorrectas — inténtalo de nuevo"; la password no queda en ningún sitio (FR-6).

### Flow 3 — Apagado con advertencia (Roberto, desde el sofá)

1. Roberto abre la app; el NAS muestra "Encendida".
2. Toca `Apagar`.
3. **Dialog de advertencia**: "¿Apagar NAS? No podrás acceder a él hasta que alguien lo encienda." — botones `Cancelar` / `Apagar`.
4. Confirma; el botón muestra progreso.
5. Snackbar "Máquina apagada"; el estado pasa a gris (por confirmación local o por evento del stream).
6. **Climax:** el apagado fue consciente y confirmado — nada de apagones accidentales.

Fallo: la máquina no apaga (sin sudo NOPASSWD) → snackbar "No se pudo apagar: revisa los permisos SSH de <máquina>" (el alta ya avisó de este requisito).

### Flow 4 — El agente IA apaga la NAS desde la tailnet (Roberto, notificado)

1. Roberto tiene la app abierta (o en segundo plano); la NAS muestra "Encendida".
2. Un agente IA (MCP, FR-16) apaga la NAS vía el BE.
3. El BE emite el evento de estado con origen `mcp` (SSE, FR-18); la fila de la NAS pasa a "Apagada" sin interacción de Roberto.
4. La app muestra una **notificación del sistema**: "NAS — El agente apagó la máquina" (también con la app en segundo plano).
5. **Climax:** Roberto sabe quién apagó la NAS aunque no esté mirando la app — la notificación conecta la acción del agente con su dispositivo.

Fallo: stream caído → la notificación y la actualización llegan con el polling (≤30 s); permiso de notificaciones denegado → solo cambia la fila, sin aviso.

## Inspiration & Anti-patterns

- **Lifted from Home Assistant:** el estado visible de un vistazo con acciones sin fricción; una lista que es el panel.
- **Lifted from las apps de IoT de casa (Philips Hue, TP-Link):** el patrón fila + toggle/acción inline, familiar para cualquier usuario de smart home.
- **Rejected — pantalla de detalle por máquina:** fricción innecesaria para un control de 2 acciones; el detalle técnico (IP/MAC) cabe en la fila.
- **Rejected — estética "hacker"/terminal:** la app la usa gente sin contexto técnico; el microcopy nunca expone protocolos.
- **Rejected — apagado sin confirmación:** una máquina apagada en remoto puede quedar inaccesible durante horas; todo apagado pide confirmación (requisito del PRD vía UJ-3).
