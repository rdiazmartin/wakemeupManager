---
title: wakemeupManager
status: final
created: 2026-09-13
updated: 2026-09-13
revision: r3 — update MCP (sección 4.6, FR-16..18, FR-10b, canal mcp en FR-11, MVP 6.1); requerimientos del usuario añadidos en sesión de trabajo.
---

# PRD: wakemeupManager

## 0. Document Purpose

Este PRD define el producto **wakemeupManager**: una herramienta gratuita y autohospedada para encender y apagar máquinas de la red doméstica desde el móvil, estando dentro o fuera de casa. Lo consumen el equipo de desarrollo (backend + app Android), y es la base para los artefactos downstream: UX, arquitectura, epics y stories. Cubre además la superficie para agentes IA: el BE expone un servidor MCP accesible desde dentro de la VPN, y los cambios de estado (vengan del MCP, de la app o del escaneo) se transmiten en tiempo real a la app. La documentación usa el vocabulario del Glosario (§3) de forma exacta; decisión clave del producto: el tráfico WOL nunca atraviesa la VPN — el backend residente en la LAN física es siempre el emisor de los magic packets.

## 1. Vision

wakemeupManager es el "interruptor remoto" de la red de casa. Un backend en Python (el BE) corre de forma permanente en una Raspberry Pi conectada a la LAN física. Descubre las máquinas de la red escaneando el rango de IPs (IP, MAC, hostname y estado online/offline), y actúa sobre ellas por dos vías: **encendido** enviando un magic packet WOL al broadcast real de la LAN, y **apagado** por SSH con la clave pública que el propio BE instala en cada máquina durante el alta.

La app Android es la superficie de control: lista las máquinas detectadas, muestra su estado y ofrece un botón encender/apagar por máquina. La conectividad entre la app y el BE viaja por una red superpuesta (p. ej. Tailscale), de modo que funciona tanto en casa como fuera de ella — pero el despertado en sí siempre ocurre en la LAN física, donde el magic packet es efectivo. *[ASSUMPTION: Tailscale (o VPN equivalente) es el transporte app→BE; el WOL es siempre local y nunca la usa.]*

Por qué importa: hoy despertar una máquina en remoto exige soluciones cerradas o fragmentadas (apps de escritorio, MACs a mano, scripts propios), y apagarla en remoto ni siquiera forma parte de la mayoría de ellas. wakemeupManager cubre el ciclo completo en una pieza propia, gratuita y sin dependencias de terceros, con un patrón de manejo de credenciales seguro (la password SSH es de un solo uso).

## 2. Target User

### 2.1 Jobs To Be Done

- Funcional: encender una máquina de casa desde el móvil, estando fuera (o sin levantarme).
- Funcional: apagarla desde el sofá en lugar de ir a la máquina o usar SSH desde el teclado.
- Funcional: tener un inventario automático de las máquinas de la red con su IP/MAC/estado.
- Social: compartir el control de las máquinas con las personas de casa **sin** exponerles credenciales SSH.
- Emocional: "opero mi red desde el bolsillo, con mis propios medios".

### 2.2 Non-Users (v1)

- Personas fuera del hogar/red (producto autohospedado, no SaaS).
- Usuarios de iOS (app solo Android en v1).
*[ASSUMPTION: no hay roles ni permisos por máquina en v1: todo dispositivo con token válido ve y controla las mismas máquinas.]*

### 2.3 Key User Journeys

- **UJ-1. Roberto enciende el desktop estando de viaje.**
  - Persona + contexto: Roberto, administrador de la red, fuera de casa con el ordenador de sobremesa apagado.
  - Estado de entrada: app autenticada contra el BE vía tailnet; el token guardado se reutiliza sin re-login (FR-15).
  - Camino: abre la app → el listado se carga del BE → pulsa "Encender" en el desktop → el BE envía el magic packet al broadcast local → refresco de estado.
  - Clímax: la máquina aparece como online (verde) al cabo de segundos.
  - Resolución: cierra la app; la máquina queda arrancada.
  - *Edge case:* si la máquina ya está online, el botón Encender se muestra inactivo y no reenvía nada (FR-13); el envío es único y sin reintento automático — si tras refrescar el estado la máquina sigue offline, el usuario reintenta pulsando de nuevo.

- **UJ-2. María da de alta el portátil de la familia.**
  - Persona + contexto: María, miembro del hogar, sin conocimientos de SSH, con credenciales de la máquina en la mano.
  - Estado de entrada: autenticada; la máquina ya aparece en el listado como "descubierta" (el descubrimiento es automático).
  - Camino: toca la máquina → "Dar de alta" → introduce usuario y password SSH → el BE hace `ssh-copy-id` de una vez y fija el fingerprint del host → la password se descarta.
  - Clímax: la máquina pasa a "gestionada" con los botones activos; María no ha visto jamás una clave.
  - Resolución: puede encenderla/apagarla como el resto.
  - *Edge case:* password incorrecta → error claro y la password **igualmente** se descarta; hay que reintroducirla. Máquina inalcanzable → mensaje "máquina no responde, ¿está encendida y con SSH activo?".

- **UJ-3. Roberto apaga el NAS desde el sofá.**
  - Persona + contexto: Roberto, final de la tarde, sin ganas de ir al cuarto.
  - Estado de entrada: gestión ya realizada (clave instalada y fingerprint fijado).
  - Camino: abre la app → toca el NAS → "Apagar" → confirmación → el BE ejecuta apagado vía SSH.
  - Clímax: el estado pasa a offline.
  - Resolución: listo; no requiere nada más.
  - *Edge case:* la máquina no apaga (sudo NOPASSWD no concedido) → el BE registra el fallo y lo muestra; el flujo de alta advierte de este requisito.

## 3. Glossary

- **BE (backend)** — servicio Python residente que corre permanentemente en la Raspberry Pi, dentro de la LAN física, como usuario dedicado no-root. Única entidad autorizada para enviar WOL y conectarse por SSH.
- **Máquina descubierta** — máquina detectada por el descubrimiento del BE con IP/MAC/hostname registrados; todavía no se puede apagar.
- **Máquina gestionada** — máquina descubierta tras completar el **alta**; la clave pública del BE está instalada, el **fingerprint de host** está fijado y puede apagarse y gestionarse.
- **Descubrimiento** — escaneo del **rango de IPs** configurado sobre la LAN física para detectar máquinas y obtener IP/MAC/hostname: barrido ICMP + lectura de `/proc/net/arp`. Funciona dentro de un único segmento L2 (sin cross-subnet) y se ejecuta como único proceso a la vez.
- **Magic packet** — trama WOL de capa 2 (trama Ethernet con 6×0xFF + MAC destino repetida 16 veces) que enciende una máquina con WOL habilitado. Solo funciona en el dominio de broadcast físico; **no cruza VPN**.
- **Broadcast real de la LAN** — dirección de broadcast de la subred física donde reside el BE; destino exclusivo de los magic packets.
- **Tailnet / VPN** — red superpuesta (p. ej. Tailscale) usada como transporte **app → BE**. El tráfico WOL nunca la utiliza y el BE solo escucha peticiones API en ella.
- **Clave SSH del BE** — par de claves del servicio; la pública se instala en cada máquina gestionada para el apagado sin password.
- **Alta (enrollment)** — proceso por el que el BE copia su clave pública a una máquina descubierta usando una **password de un solo uso** (nunca almacenada) y fija el **fingerprint de host** de la máquina.
- **Password de un solo uso** — credencial remota introducida por el usuario durante el **alta**; el BE la usa una única vez para `ssh-copy-id` y la descarta, exitosa o fallida, sin dejarla en disco, logs ni en su almacenamiento.
- **Fingerprint de host** — huella de la clave de host SSH de la máquina remota, capturada en el **alta** y fijada a la máquina; si cambia, el BE no ejecuta apagados sobre ella.
- **Estado** — online/offline de una máquina según comprobación del BE (ICMP/ARP) con TTL configurable.
- **Token de dispositivo** — credencial de API única por dispositivo Android, generada y revocada individualmente en el BE; distinta del token de cualquier otro dispositivo.

## 4. Features

### 4.1 Descubrimiento de máquinas

**Description:** El BE escanea el rango de IPs configurado en su LAN física y registra las máquinas encontradas: IP, MAC y hostname, con su estado. El escaneo se dispara de forma periódica y puede forzarse desde la app. El inventario resultante alimenta el listado de la app, que distingue máquinas descubiertas vs. gestionadas. Realiza UJ-1, UJ-2, UJ-3 (base de todas).

**Functional Requirements:**

#### FR-1: Escaneo del rango configurado

El BE escanea el rango de IPs configurado en la LAN física y registra IP, MAC y hostname de cada máquina que responde.

**Consequences (testable):**
- Ejecutado el escaneo sobre un rango con N máquinas conectadas, el inventario contiene todas las que responden a ICMP o aparecen en `/proc/net/arp`, con MAC válida y hostname (si resuelve).
- La técnica de descubrimiento no requiere privilegios de root: barrido ICMP + lectura de `/proc/net/arp`.
- Las direcciones de las interfaces propias del BE (incluida la interfaz tailnet y loopback) y las direcciones no-LAN se excluyen del inventario.
- Un escaneo en curso no bloquea peticiones de encendido/apagado; los escaneos concurrentes se serializan (singleflight: solo uno en ejecución, los demás esperan) y no corrompen el inventario.

**Out of Scope:**
- Escaneo multi-segmento o a través de la tailnet (el descubrimiento es L2, misma LAN física).

#### FR-2: Estado online/offline

El BE mantiene el estado online/offline de cada máquina del inventario mediante comprobación periódica (ICMP/ARP).

**Consequences (testable):**
- El estado se comprueba cada 30 s (mitad del TTL por defecto) y se considera caduco a los 60 s.
- El TTL es configurable entre 15 s y 300 s; la comprobación periódica se ajusta proporcionalmente (TTL/2).
- El estado puede consultarse vía API.
- Máquinas en tailnet pero fuera del segmento local se reportan según su presencia en la LAN física, no por su estado en la VPN.

#### FR-3: Escaneo periódico y forzado

El BE re-dispara el descubrimiento automáticamente cada 10 minutos (intervalo configurable) y además puede forzarse desde la app Android en cualquier momento.

**Consequences (testable):**
- Pasados 10 minutos de un escaneo completado, el BE inicia otro sin intervención; con el intervalo configurado a otro valor, se respeta dicho valor.
- `POST /scan` (invocado desde la app) inicia un escaneo inmediato y devuelve su resultado; si un escaneo periódico está en curso, el forzado espera a que termine (singleflight, FR-1).
- Máquinas que desaparecen del segmento se marcan como offline; solo desaparecen del inventario mediante borrado explícito vía API (`DELETE /machines/{id}`, uso avanzado). El borrado no está disponible en la UI de la app en v1.

### 4.2 Wake on LAN

**Description:** El BE envía el **magic packet** a la MAC de la máquina objetivo con destino el **broadcast real de la LAN**. El envío ocurre siempre desde el propio BE (residente en el LAN físico): la app solo dispara la acción vía API, por lo que el WOL nunca viaja por la **tailnet**. Realiza UJ-1.

**Functional Requirements:**

#### FR-4: Envío de magic packet al broadcast local

El BE construye y envía el magic packet para la MAC de la máquina hacia la dirección de broadcast de su subred local, en un único envío por petición.

**Consequences (testable):**
- Disparado `POST /machines/{id}/wake`, el BE emite el paquete correcto (MAC destino repetida 16 veces sobre 6×0xFF) hacia el broadcast de la subred.
- La MAC se valida antes de enviar (12 dígitos hex, no multicast, distinta de cero): MAC inválida → HTTP 422 y no se emite paquete.
- El envío es único por petición (sin reintento automático) y se registra en el log del BE (hora, máquina, destino).
- El endpoint responde éxito aunque la máquina tarde en arrancar; el éxito real se confirma por el estado (FR-2).

**Out of Scope:**
- Unicast WOL por IP, wake a través de la VPN, o envío directo desde el móvil.

#### FR-5: Dependencias de red documentadas

El BE usa el medio que hace fiable el WOL (interfaz cableada) y comunica las condiciones del entorno.

**Consequences (testable):**
- Si existe interfaz Ethernet, el WOL se envía solo por ella y `GET /status` expone el nombre de la interfaz emisora.
- En ausencia de interfaz Ethernet, el BE reporta estado `warning` (WOL no fiable vía WiFi) sin bloquear el resto de funciones.
- La documentación de alta indica cómo verificar WOL en firmware/BIOS. *[ASSUMPTION: WOL habilitado en firmware/BIOS de las máquinas objetivo está fuera del control del sistema, pero se documenta cómo verificarlo.]*

### 4.3 Apagado por SSH con alta segura

**Description:** Para poder apagar una máquina, primero hay que **darle de alta**: un usuario con credenciales introduce **usuario + password SSH** desde la app; el BE ejecuta `ssh-copy-id` con esa password (de un solo uso, entregada por fichero temporal con permisos 600) y la **descarta inmediatamente**, sea el alta exitosa o fallida. En el alta el BE fija el **fingerprint de host** de la máquina. Con la clave instalada, el apagado se ejecuta por SSH con el comando adecuado (Linux v1). Realiza UJ-2, UJ-3.

**Functional Requirements:**

#### FR-6: Alta con password de un solo uso

El BE copia su clave pública a la máquina objetivo usando la password SSH proporcionada, sin almacenarla, y fija el fingerprint de host de la máquina. El alta se ejecuta en proceso con `asyncssh` (sin subprocesos ni `sshpass`).

**Consequences (testable):**
- La password se usa una única vez (propiedad de diseño; se verifica su ausencia de persistencia) y no queda en disco, en los logs, en la memoria de configuración ni en el almacenamiento (SQLite) del BE, ni en fallo ni en éxito. Solo existe en memoria del proceso durante el alta.
- La copia de clave se hace vía SFTP (`asyncssh`): si la clave pública del BE no está en `authorized_keys` del usuario remoto, se añade (append idempotente). No hay paso de password por argumentos ni variables de entorno (no visible en `ps` ni `/proc`). Se documenta la exposición residual en memoria del proceso durante la conexión.
- Alta exitosa: la máquina pasa a **gestionada**, la clave pública del BE queda en `authorized_keys` del usuario remoto y el fingerprint de host de la máquina queda fijado junto a sus datos.
- Alta fallida: error claro al usuario ("credenciales incorrectas" / "máquina inaccesible") y la máquina permanece descubierta.

#### FR-7: Apagado por SSH

El BE apaga una máquina gestionada ejecutando el comando de apagado correcto por SSH con la **clave SSH del BE**, solo si el fingerprint de host actual coincide con el fijado en el alta.

**Consequences (testable):**
- Disparado `POST /machines/{id}/shutdown` en una máquina gestionada, el BE verifica el fingerprint de host contra el fijado y, si coincide, ejecuta el comando vía SSH sin password; la máquina pasa a offline.
- Si el fingerprint no coincide (IP reasignada por DHCP u host sospechoso): no se ejecuta el apagado, la máquina se marca como no fiable con estado `offline` y el evento se registra.
- Linux: usa `shutdown`/`systemctl poweroff`. Requisito documentado en el flujo de alta: el usuario remoto debe tener `sudoers NOPASSWD` restringido al comando de apagado; sin él, el apagado falla con error claro. *[ASSUMPTION: en v1 las máquinas a apagar son Linux.]*
- El resultado se registra en el log.

**Out of Scope:**
- Windows/macOS (v2), apagado por otros medios (Wake-on-WAN inverso, remoto de escritorio).

#### FR-8: Gestión de la clave del BE

El BE guarda su par de claves con permisos restringidos y solo el servicio las usa. El BE se ejecuta como un usuario dedicado no-root en la Raspberry. *[ASSUMPTION: el BE gestiona un único par de claves SSH (no una por usuario ni por dispositivo).]*

**Consequences (testable):**
- La clave privada reside con permisos 600 y el proceso del BE (usuario dedicado no-root) es el único que la lee.
- No se expone endpoint alguno que devuelva claves o las reexporte.

### 4.4 API REST y acceso

**Description:** El BE expone una API REST usada exclusivamente por la app Android, protegida con **tokens de dispositivo**. El BE escucha únicamente en la interfaz de la **tailnet** y en loopback; la comunicación app→BE viaja cifrada por la VPN (WireGuard) y no se expone a la LAN física. Realiza UJ-1, UJ-2, UJ-3.

**Functional Requirements:**

#### FR-9: Superficie de API REST

El BE expone los endpoints de descubrimiento, máquinas, estado, encendido, apagado, alta y el catálogo de errores.

**Consequences (testable):**
- Endpoints cubiertos: listado de máquinas con estado, escaneo (disparo y consulta — con escaneo en curso, `POST /scan` responde 202 con estadísticas del escaneo en marcha), alta de máquina, encendido, apagado, y estado/versión del propio BE.
- Respuestas en JSON con vocabulario del Glosario; catálogo de errores acotado: 400/422 (cuerpo inválido, MAC inválida), 401 (token inválido o revocado), 404 (máquina inexistente), 409 (alta ya realizada), 429 (límite de autenticación), 5xx con motivo en el cuerpo.
- El BE escucha solo en la interfaz de la tailnet (p. ej. la dirección Tailscale) y loopback; ninguna interfaz de la LAN física ni WiFi expone la API.

#### FR-10: Autenticación por token de dispositivo

Toda petición requiere un **token de dispositivo** válido; el BE no acepta peticiones anónimas y protege la autenticación contra fuerza bruta.

**Consequences (testable):**
- Petición sin token o con token inválido o revocado → HTTP 401.
- Cada dispositivo Android tiene su propio token, generado y revocado individualmente vía CLI del BE (rotación sin afectar a otros dispositivos).
- Backoff de autenticación: tras 5 fallos consecutivos con el mismo token o dirección en 5 minutos, el BE responde HTTP 429 durante 15 minutos.
- Los eventos de autenticación fallida quedan en el log.

#### FR-10b: Token dedicado del MCP

El BE emite un **token de MCP** independiente (también por CLI, mismo formato y política de revocación) que identifica exclusivamente al agente IA.

**Consequences (testable):**
- El token de MCP es distinto de los tokens de dispositivo; revocarlo no afecta a los móviles y viceversa.
- El registro de actividad distingue las acciones del agente (canal `mcp`) de las de la app (canal `api`) — FR-11.
- El MCP rechaza peticiones sin token de MCP con 401.

#### FR-11: Registro de actividad

El BE registra las acciones relevantes (escaneos, altas, encendidos, apagados, errores de autenticación) con fecha, canal/token y resultado.

**Consequences (testable):**
- Cada acción queda en el log del BE con timestamp, canal (`api` | `mcp`), token/dispositivo, máquina y resultado (subconjunto de FR-6: los eventos de alta nunca contienen la password de un solo uso).
- Los logs permiten medir las métricas de éxito SM-1 a SM-4 sin atribución a persona alguna, contando las acciones de la app y del agente por separado.

### 4.5 App Android

**Description:** App nativa en Kotlin (Jetpack Compose) que lista las máquinas descubiertas/gestionadas con su estado, ofrece botones **Encender**/**Apagar**, el flujo de **alta** (usuario + password) y la configuración de conexión (URL del BE + token de dispositivo). Realiza UJ-1, UJ-2, UJ-3.

**Functional Requirements:**

#### FR-12: Listado de máquinas con estado

La app muestra el inventario del BE: máquina (hostname/IP/MAC), estado online/offline y estatus descubierta/gestionada. Incluye un botón de **escanear ahora** para forzar el descubrimiento (FR-3).

**Consequences (testable):**
- El listado se refresca manualmente y con polling automático cada 30 s por defecto, configurable entre 10 s y 300 s y pausable bajo ahorro de batería; muestra estado de cada una.
- Pulsar "escanear ahora" dispara `POST /scan` en el BE y actualiza el listado con el resultado.
- Fallos de conexión con el BE se indican sin romper el listado previo.

#### FR-13: Botones Encender / Apagar

Cada máquina gestionada ofrece Encender y Apagar; las descubiertas ofrecen Alta.

**Consequences (testable):**
- Encender está disponible en máquinas offline (si está online, el botón se muestra inactivo y no reenvía); Apagar solo en gestionadas online.
- Cada acción requiere confirmación y muestra el resultado (éxito, error, "máquina no responde").
- Las acciones fallidas permiten reintento sin salir de la pantalla.

#### FR-14: Alta desde la app

La app recolecta usuario + password SSH y los envía al BE para el alta; la password no se persiste en el dispositivo.

**Consequences (testable):**
- El formulario pide usuario y password, con aviso de que se usarán una sola vez.
- La password no se guarda en almacenamiento local ni en logs de la app.
- Resultado (gestionada / error con motivo) visible al usuario.

#### FR-15: Configuración de conexión y sesión

La app permite configurar dirección/URL del BE y token de dispositivo, y reutiliza la sesión sin re-login.

**Consequences (testable):**
- URL y token editables desde ajustes; al guardar, la app llama a `GET /status`: HTTP 401 → "token rechazado", timeout → "BE inalcanzable".
- El token se almacena en el keystore/almacenamiento privado de Android. *[ASSUMPTION: el token se almacena en el keystore de Android en v1.]*
- La app reutiliza el token guardado sin re-autenticar en cada arranque, salvo 401 (token revocado), que pide configurarlo de nuevo.

### 4.6 Servidor MCP para agentes IA y eventos push

**Description:** El BE expone un **servidor MCP** (Model Context Protocol) accesible solo desde dentro de la VPN (misma regla que la API: escucha solo en la interfaz tailnet). Un agente IA configurado con su **token de MCP** puede consultar máquinas, encenderlas, apagarlas y forzar escaneos; el alta de máquinas no se expone al MCP (el agente no debe tratar passwords SSH en v1). Todo cambio de estado originado por el MCP se **transmite a la app Android** vía eventos push (SSE) para que se refleje en la lista sin esperar al polling. Realiza UJ-1, UJ-3 (vía agente IA).

**Functional Requirements:**

#### FR-16: Contrato MCP (tools de v1)

El BE expone un servidor MCP con herramientas de lectura y control, sin acceso al alta.

**Consequences (testable):**
- Tools expuestas en v1: `list_machines` (inventario con estado y estatus), `get_machine_status`, `wake_machine`, `shutdown_machine`, `force_scan`.
- No se expone tool de alta: el agente no puede instalar claves ni manejar passwords SSH.
- Las tools de control (`wake_machine`, `shutdown_machine`) aplican las mismas verificaciones que la API (MAC válida, fingerprint fijado, comando restringido — FR-4, FR-7).
- El contrato MCP sigue el protocolo MCP estándar (JSON-RPC sobre transporte de la VPN).

#### FR-17: Acceso y autenticación del MCP

El MCP solo escucha en la interfaz de la tailnet (y loopback) y exige su token dedicado.

**Consequences (testable):**
- Sin token de MCP válido o sin token en absoluto → petición rechazada (401/error de protocolo).
- El token de MCP se genera y revoca por la CLI del BE, de forma independiente de los tokens de dispositivo (FR-10b); se muestra una sola vez.
- No hay acceso del MCP desde la LAN física ni desde fuera de la VPN.

#### FR-18: Eventos push de cambios de estado

El BE transmite a la app los cambios de estado (online/offline/no_fiable, alta completada, escaneo terminado) tan pronto como ocurren, vía SSE sobre la tailnet; la app lo refleja en la lista.

**Consequences (testable):**
- Cualquier cambio de estado originado por el MCP (FR-16), por la API o por el escaneo periódico dispara un evento SSE; la app actualiza la fila afectada sin esperar al polling.
- La app se suscribe al stream SSE con su token de dispositivo (auth del stream = mismo token).
- El polling (FR-12) queda como fallback y refresco manual: con la suscripción activa, el estado se refleja en ≤2 s; sin ella (stream caído, ahorro de batería), el polling de 30 s mantiene la lista.
- Si el stream se interrumpe, la app lo reconecta con backoff y el polling cubre el hueco.

**Feature-specific NFRs:**
- El stream SSE no debe impedir el modo ahorro de batería: se pausa y solo el polling queda activo (FR-12).

## 5. Non-Goals (Explicit)

- No va a ser un producto SaaS ni multi-tenant: es autohospedado.
- No va a controlar máquinas fuera de la LAN física donde vive el BE (ni vía VPN por WOL).
- No va a gestionar Windows/macOS en v1 (ni encendido ni apagado).
- No va a tener roles ni permisos por máquina en v1 (los tokens son por dispositivo, no por usuario).
- No va a ser un panel de monitorización (solo estado on/off).
- No va a programar encendidos por horario en v1 (candidato v2).
- No va a sustituir a un centro de control de la red (router/DNS/ACL).

## 6. MVP Scope

### 6.1 In Scope

- BE Python en Raspberry: descubrimiento por rango de IPs (FR-1..FR-3), WOL al broadcast local (FR-4, FR-5), alta con password de un solo uso y pinning de host key (FR-6), apagado por SSH (FR-7, FR-8), API REST autenticada solo-tailnet (FR-9..FR-11), servidor MCP para agentes IA con token dedicado y eventos push SSE (FR-16..FR-18).
- App Android (Kotlin/Compose): listado con estado (FR-12), encender/apagar (FR-13), alta (FR-14), configuración (FR-15), suscripción SSE con fallback de polling (FR-12, FR-18).
- Persistencia del inventario en SQLite; transporte app→BE por tailnet; BE como único emisor WOL.

### 6.2 Out of Scope for MVP

- Schedules/horarios de despertado — v2 (añade valor real, bajo coste). `[NOTE FOR PM]`
- Soporte Windows/macOS — v2; el BE no puede apagarlas en v1.
- Roles/permisos por usuario — v2.
- App iOS / multiplataforma — v2, decidir con Flutter si llega.
- Interfaz web de administración — v2 (el BE se administra por config + CLI en v1).
- Múltiples rangos de IPs / varios segmentos — v2.
- Monitorización avanzada (CPU/RAM/temperaturas) — out.

## 7. Success Metrics

*Medición: todas las métricas se computan a partir de los logs del BE (FR-11) y los tokens de dispositivo, sin atribuir acciones a personas.*

**Primary**
- **SM-1**: Uso — ≥2 dispositivos con token usan la app ≥3 días a la semana durante el primer mes (medible: logs de peticiones por token). Valida FR-12, FR-13.

**Secondary**
- **SM-2**: Tasa de alta — ≥80% de las máquinas que el hogar despierta/apaga quedan dadas de alta en el primer mes (medible: inventario descubierta/gestionada). Valida FR-6.
- **SM-3**: Fiabilidad del despertado — ≥95% de los encendidos disparados desde la app terminan en estado online en ≤60 s (con WOL habilitado en firmware; medible: logs de wake + transición de estado). Valida FR-4.
- **SM-4**: Apagados desde la app y el agente — ≥5 apagados/semana registrados vía API y MCP durante el primer mes (medible: logs FR-11 con canal `api`/`mcp`; denominador físico no observable, se mide solo el numerador). Valida FR-7, FR-16.

**Counter-metrics (do not optimize)**
- **SM-C1**: No optimizar la velocidad del escaneo sacrificando el inventario (p. ej. escaneos tan agresivos que las máquinas caen de la tabla o la red se satura). Contrapesa SM-2 (inventario completo y estable).
- **SM-C2**: No acumular features de monitorización bajo el paraguas de "estado" — el estado es solo on/off. Contrapesa SM-1.

## 8. Open Questions

1. ¿Rotación de la clave SSH del BE: política y procedimiento cuando se rota manualmente (v2)? — La rotación no se automatiza en v1.
2. ¿Bloqueo biométrico/pin de la app Android en v2? — En v1 el acceso a la app queda protegido por el patrón del sistema.

## 9. Assumptions Index

- §1 — Tailscale (o VPN equivalente) es el transporte app→BE; el WOL es siempre local y nunca usa la VPN.
- §2.2 — Sin roles ni permisos por máquina en v1; todo dispositivo con token válido ve y controla las mismas máquinas.
- FR-2 — Comprobación de estado cada TTL/2; default 60 s TTL configurable 15–300 s.
- FR-3 — Las máquinas del inventario se conservan aunque no estén presentes (solo se borran explícitamente).
- FR-5 — WOL habilitado en firmware/BIOS de las máquinas objetivo está fuera del control del sistema, pero se documenta cómo verificarlo.
- FR-7 — En v1 las máquinas a apagar son Linux.
- FR-8 — El BE gestiona un único par de claves SSH (no una por usuario ni por dispositivo).
- FR-15 — El token se almacena en el keystore de Android.
