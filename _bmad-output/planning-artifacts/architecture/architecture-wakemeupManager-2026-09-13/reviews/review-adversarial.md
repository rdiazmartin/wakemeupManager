# Review adversarial — ARCHITECTURE-SPINE.md (wakemeupManager)

Método mandatado: por cada pareja de unidades se construyen **dos unidades independientes** (A y B), cada una cumple *todos* los AD a la letra, y aun así sus builds resultan incompatibles. Cada hallazgo declara la divergencia (unidad A vs unidad B), si algún AD actual la bloquea o deja de cubrirla, y la recomendación (AD nuevo/endurecido o entrada Deferred).

## Verdict

El spine fija bien el paradigma (hexagonal, contracto=API) y la identidad de máquina (AD-2), pero deja sin dueño exactamente los contratos que dos equipos independientes necesitan para acoplar: la identidad SSH efectiva (usuario remoto, ruta de clave, formato de fingerprint), el cuerpo y la semántica de concurrencia de `POST /scan`, el esquema del DTO de estado de máquina (en especial `no_fiable`), y el comando de apagado/suders como configuración. El resultado más probable: el apagado **siempre falla** (clave instalada en un usuario distinto del que usa shutdown), la lista de la app no puede mostrar "No fiable" y ofrece Apagar donde el BE lo rechaza (AD-2/AD-4), y "escanear ahora" se topa con 409 cuando el singleflight le pide esperar. Ningún AD reconcilia estos pares; hacen falta AD-9 (contrato DTO + semántica /scan), AD-10 (identidad y operación del apagado SSH) y un AD-11 o Deferred para el envelope de arranque.

---

## F1 — [critical] La identidad SSH de apagado no tiene dueño: `Machine` es {id, fingerprint} pero el apagado necesita usuario remoto, clave y binding de fingerprint

- **Pareja:** BE unidad A (`services/enrollment`) vs BE unidad B (`services/shutdown`), ambos sobre `adapters/ssh`.
- **Unidad A (cumple AD-5, FR-6):** conecta por SSH a la IP actual de la máquina como **usuario `u_maria`** (el que la app recogió), instala la clave en `~u_maria/.ssh/authorized_keys`, y persiste en la fila `Machine` solo lo que AD-2 declara: `id` + `host_fingerprint`. El usuario no está en el modelo → no se guarda (cumple AD-2 a la letra: "Machine = id entero (PK) + host_fingerprint").
- **Unidad B (cumple AD-2, AD-4, AD-7):** carga de SQLite `id` + `host_fingerprint`, y para conectarse usa el usuario de configuración `[ssh] user` de `config.toml` (sección que la Convención de Config sí lista) — p. ej. `u_maria` **no** es el del config, o es `pi`. La clave privada la lee de `/var/lib/wakemeup/ssh/id_rsa` (600, fuera de SQLite, cumple AD-7); el alta instaló la pública del par `id_ed25519` que A generó.
- **Colisión:** la clave pública instalada no corresponde a la privada que B carga, o el usuario no coincide: **todo apagado de toda máquina falla en producción**, sin violar un solo AD. Ningún AD fija: (1) la persistencia del `ssh_username` de alta en `Machine`, (2) la ruta/ubicación de la clave privada (AD-7 solo impone permisos 600/700 y "fuera de SQLite"), (3) el algoritmo del par (ed25519 vs RSA).
- **Además (mismo hueco):** AD-2 dice fingerprint = "SHA-256 de la clave de host remota". A guarda el digest de la clave pública en hex; B compara contra el fingerprint OpenSSH `SHA256:base64…` que le devuelve asyncssh, o compara la entrada de `known_hosts` **bind-por-IP** mientras el alta fijó el fingerprint de la clave (AD-2: "la IP es efímera"). Cualquier variante → mismatch permanente → todas las máquinas a `no_fiable`. Formato serializado (hex / base64 / prefijo `SHA256:`), binding de la verificación (clave desnuda vs hostname vs IP) y quién captura el fingerprint de referencia en el alta son todos no-pinned.
- **¿Lo arregla algún AD?** No. AD-2 define el modelo mínimo pero no el formato del fingerprint ni el binding; AD-5 no dice dónde queda el `ssh_username`; AD-7 no fija la ruta de la clave; AD-4 asume "la clave BE" sin enlazarla al par instalado.
- **Recomendación (AD nuevo):** **AD-9 — Identidad y operación SSH de apagado**: `Machine` persiste `ssh_username` capturado en el alta (AD-5 lo escribe; AD-4/servicio shutdown lo lee); el par BE se fija como `id_ed25519` en `/var/lib/wakemeup/ssh/` (AD-7 lo amplía con ruta y algoritmo; la rotación queda en Deferred); el fingerprint fijado se almacena como hex-lower de SHA-256 **del blob de la clave de host desnuda** (sin prefijo, sin base64) y la verificación de AD-4 compara ese digest contra la clave obtenida en el handshake — nunca contra `known_hosts` keyed por IP/hostname.

---

## F2 — [high] `POST /scan` no tiene contrato: el cuerpo de respuesta y la semántica de concurrencia que ve la app no están decididos

- **Pareja:** BE unidad (`services/discovery` + `api`, AD-3) vs app unidad (`ui` lista + "escanear ahora", AD-8).
- **Unidad A (cumple FR-3 "el forzado espera a que termine (singleflight)" y AD-3 "ambos serializados sobre el mismo lock"):** el endpoint **bloquea** hasta que el escaneo en curso termina y devuelve 200 con el resultado.
- **Unidad B (cumple el catálogo FR-9, que lista 409 "escaneo en curso", y AD-3 "un único escaneo a la vez"):** devuelve **409 inmediato** si hay escaneo de otro origen, y no bloquea.
- **Colisión visible para el usuario:** con B, "escanear ahora" (EXPERIENCE: botón con loader sincrónico, "gira durante el escaneo") se corta a los pocos ms con un snackbar de error mientras el escaneo periódico corre; con A, el spinner aguanta hasta 10 min (intervalo del periódico) con el `POST` colgado sobre el rate-limit... La app de A/B tampoco sabe qué parsear: FR-13/FR-12 dice que el listado "se actualiza con el resultado", pero **ningún AD fija el cuerpo de `POST /scan`**: ¿inventario completo (`{"machines":[…]}`), solo estadísticas (`{"scanned": 42}`), o 202 + `GET /scan` posterior? La app (AD-8) que parsee inventario de A y la app que espere stats de B se rompen mutuamente.
- **¿Lo arregla algún AD?** No. AD-3 cubre el comportamiento *interno* (singleflight, upsert) pero no la respuesta HTTP; AD-1 redirige a FR-9, que lista 409 en el catálogo sin decir cuándo; FR-3 y FR-9 están en contradicción (esperar vs 409). El catálogo tampoco distingue 429 (límite de autenticación, AD-6) de un eventual throttle de escaneo.
- **Recomendación (AD endurecido — AD-3 o nuevo):** fijar: `POST /scan` → 200 con inventario completo `{"machines":[…]}` y semántica **block-until-done con timeout** (p. ej. 60 s); si no puede completar, 409 `{"error":{"code":"scan_in_progress"}}`; prohibido 429 para escaneo (429 queda reservado a autenticación, AD-6). La app mapea 409 → "Escaneo ya en curso" y conserva el listado. Deferred: `GET /scan` para resultados asíncronos.

---

## F3 — [high] `no_fiable` existe en el spine pero no en el DTO: la UI muestra "Apagar" cuando el BE rechaza

- **Pareja:** app unidad A (`ui`, AD-8 + EXPERIENCE) vs app unidad B (`data/remote`, DTOs del contrato, AD-1).
- **Unidad A (cumple AD-8 y EXPERIENCE, fila "Máquina no fiable"):** espera un estado explícito para pintar el badge ámbar "No fiable" y **no ofrecer Apagar**.
- **Unidad B (cumple FR-2 y FR-12, que solo definen "estado online/offline", y el Glosario, que define `no_fiable` como atributo de la máquina):** el DTO serializa `status: online|offline` + `gestionada: bool`. `no_fiable` (AD-2/AD-4) llega como `status=offline, gestionada=true` — o como booleano `reliable`, ausente del DTO.
- **Colisión exacta (el riesgo de AD-2/AD-4 que el spine no cierra):** la máquina `no_fiable` se presenta como "Apagada · gestionada". La UI (FR-13) habilita **Encender** (offline) y, en cuanto el próximo ping la pone online, **Apagar** (gestionada+online). El BE **rechaza** el apagado (AD-2/AD-4, fingerprint mismatch → se queda en `no_fiable`), mientras que cada wake/shutdown exitoso en apariencia re-dispara el ciclo. El usuario ve "Apagada" y CTA activos donde el BE "no se actúa sobre ella" (AD-2). Si a B se le ocurre serializar `no_fiable` como otro valor del enum de *gestión* (`descubierta|gestionada|no_fiable`), la UI de A (que espera un eje de reachability) tampoco lo dibuja.
- **¿Lo arregla algún AD?** No. AD-8 remite a EXPERIENCE, que describe el estado visual pero no su encaje en el DTO; AD-2/AD-3 definen las transiciones (`descubierta↔gestionada`, `online/offline/no_fiable`, Tabla de Convenciones) sin fijar cómo se serializan en JSON; AD-1 solo impone el catálogo FR-9 y "vocabulario del Glosario", no el esquema de máquina.
- **Recomendación (AD nuevo):** **AD-10 — DTO de máquina**: `{"id", "hostname", "ip", "mac", "estado": "online|offline", "confiabilidad": "fiable|no_fiable", "gestionada": bool}`; regla: `confiabilidad=no_fiable` → la app **no ofrece Apagar ni Encender** (solo Alta/reintento, conforme EXPERIENCE), e invariante en el BE: nunca rechazar una acción que la UI muestre habilitada (la UI deriva las habilitaciones del par (estado, confiabilidad, gestionada) y AD-4 verifica la misma tripleta). En Deferred: reintento de alta desde `no_fiable`.

---

## F4 — [high] El allowlist del comando de apagado y su config no tienen dueño: sudoers exige la invocación exacta y nadie la fija

- **Pareja:** BE unidad A (`services/enrollment`, que documenta el prerrequisito, FR-7) vs BE unidad B (`services/shutdown` + `adapters/ssh`, AD-4).
- **Unidad A (cumple FR-7/flujo de alta):** advierte al usuario que conceda `sudoers NOPASSWD` **restringido al comando de apagado** — y documenta como ejemplo `systemctl poweroff`.
- **Unidad B (cumple AD-4, que autoriza explícitamente cualquiera de los dos: "comando fijo configurado (`systemctl poweroff` / `shutdown -h now`)"):** ejecuta vía SSH `sudo -n shutdown -h now`.
- **Colisión:** una entrada de sudoers con lista de comandos coincide *solo* con la invocación exacta (mismo binario realpath, mismos argumentos, misma forma del sudo). Documentar un comando y ejecutar el otro, o ejecutar `sudo shutdown…` donde el sudoers concedió solo `systemctl`, produce fallo **seguro** en cada apagado — con la app mostrando el error de FR-13. Además: ¿quién lee `[ssh] shutdown_cmd` de `config.toml`? El paradigma hexagonal dice "servicios sin IO"; la Convención de Config lista la sección `[ssh]`, pero el seed estructural no tiene `adapters/config`; si el adaptador ssh lee config y compone el comando, y la documentación del alta lo fija de otra forma, vuelve a romper el match "documentado vs ejecutado".
- **¿Lo arregla algún AD?** No. AD-4 deja dos comandos alternativos "configurados" sin decir dónde se configuran, quién los valida contra lo documentado, ni la forma del wrapper `sudo -n <cmd>`; AD-5 no documenta el prerrequisito con el comando exacto; la capa de config no está en el seed (AD-7 cubre solo el fichero fuera de SQLite).
- **Recomendación (AD endurecido — AD-4):** fijar invocación canónica única: `sudo -n systemctl poweroff` (v1, Linux), propiedad de la cadena completa en `config.toml [ssh]` leída por `adapters/ssh` (añadir `adapters/config` al seed), y el flujo de alta documenta *esa misma* cadena como entrada sudoers `NOPASSWD` exacta. Deferred (v2): múltiples comandos por SO — ya está en Deferred el shutdown multi-SO.

---

## F5 — [high] Ciclo de vida del token: el pre-imagen del hash, salt y caso pueden divergir entre cli, api y db

- **Pareja:** BE unidad A (`cli`, alta de tokens, AD-6) vs BE unidad B (`api` validando + `adapters/db`, AD-6/AD-7).
- **Unidad A (cumple AD-6 "32 B aleatorios, hash SHA-256 en SQLite" y la Convención "token 32 B hex mostrado una sola vez"):** genera `os.urandom(32)`, muestra 64 caracteres hex a la pantalla, y persiste `sha256(token_raw_bytes)`.
- **Unidad B (cumple las mismas AD):** por ser "32 B hex", hashea la **cadena hex** (`sha256(token.hex())`) — o aplica sal por fila (`bcrypt`-style) porque AD-6 no prohíbe sal y su lectura de "hash" es más segura —, y compara case-insensitive o no, sin normalización.
- **Colisión:** todo token generado por A devuelve 401 en la API de B (y al revés), o los tokens revocados por A no coinciden con el hash que B consulta. El 429 de AD-6 (5 fallos → 15 min) agrava: el usuario queda bloqueado *por el propio diseño*. No hay AD que fije: (1) pre-imagen del hash (bytes crudos vs string hex), (2) lowercase/uppercase, (3) sal sí/no (y dónde se guarda), (4) a qué se aplica el 429 en el caso token-vs-dirección (FR-10: "con el mismo token o dirección" — ¿dos contadores separados? ¿persistidos? ¿se resetean al reiniciar el BE?).
- **¿Lo arregla algún AD?** No. AD-6 fija longitud y algoritmo pero no el pre-imagen ni sal; AD-7 solo dice dónde vive el hash.
- **Recomendación (AD endurecido — AD-6):** pre-imagen = los **32 bytes crudos**; almacenar `sha256(raw)`.hex() en lowercase; sin sal en v1 (token de alta entropía, 32 B → sal innecesaria; anotarlo como decisión explícita para no re-optimizar); comparación constante-time sobre lowercase; contadores de 429 por token **y** por dirección con persistencia en SQLite (tabla de eventos de auth, FR-11) para sobrevivir reinicios; documentar en qué unidad vive la comprobación (api → adapters/db) para que cli no duplique lógica de hashing.

---

## F6 — [medium] Dirección de broadcast e interfaz WOL: la config no tiene sección que la lleve y nadie decide entre derivar y configurar

- **Pareja:** BE unidad A (`services/wake`, AD-4) vs BE unidad B (`adapters/net`), más `services/status` para GET /status.
- **Unidad A (cumple AD-4 "broadcast de la subred física, por interfaz Ethernet si existe (FR-5)"):** deriva el broadcast en runtime del `addr/mask` de la NIC (p. ej. `192.168.1.255` para `192.168.1.10/24`).
- **Unidad B (cumple AD-4 con lectura literal de "configuración"/FR-5 y la Convención de Config):** lee `broadcast_addr` de `config.toml` — pero la Convención de Config solo lista `[scan] [api] [ssh] [auth]`; **no existe `[net]`**, y el seed tampoco tiene adaptador de config. Si B configura `192.168.0.255` (o un VLAN/segundo segmento), el magic packet muere en un broadcast equivocado o en el segmento erróneo: wake "éxito" (200, FR-4) sin encender nada.
- **Colisión latente adicional:** "interfaz Ethernet si existe" — ¿cuál si hay dos (eth0 + USB dongle)? ¿y quién declara el estado `warning` de FR-5 (sin Ethernet) en `GET /status`: `services/status` (AD-3 no lo menciona) o `services/wake`? No hay dueño del campo "interfaz emisora" que FR-5 exige exponer.
- **¿Lo arregla algún AD?** No. AD-4/AD-5 remiten a FR-5 sin fijar fuente (derivada vs configurada), ni el campo de status, ni la selección entre NICs.
- **Recomendación (AD endurecido — AD-4):** el broadcast se **deriva** siempre de la máscara de la interfaz emisora (nada de config: elimina el riesgo de mismatch config/realidad; el "broadcast real" del Glosario queda así auto-consistente); selección de NIC = preferencia estable (índice/name de la primera Ethernet con enlace, enlaces de orden determinista por `ip route`) y el resto de NICs v2/Deferred; `GET /status` expone el campo `wol_interface` bajo gobierno de AD-3 (nuevo campo, no libre).

---

## F7 — [medium] La transición descubierta→gestionada no tiene un escritor único, y `no_fiable` solo se alcanza con un intento de apagado

- **Pareja:** BE unidad A (`services/enrollment`, AD-5) vs BE unidad B (`services/discovery/status`, AD-3).
- **Unidad A (cumple FR-6):** tras el alta exitoso escribe `gestionada=True` en la fila de la máquina (upsert por `host_fingerprint`/MAC).
- **Unidad B (cumple AD-3 "upsert de IP/MAC/hostname"):** el escaneo periódico upserta por **IP** (o por MAC) la fila que vio; como AD-2 declara la IP efímera, si una máquina gestionada cambia de IP entre escaneos, B crea una **fila nueva** (nuevo PK) — la lista acaba con el par imposible: fila gestionada-offline (IP vieja, ausente) + fila descubierta-online (IP nueva). La app (AD-8) muestra ambas; el usuario intenta el alta de la descubierta y recibe 409 "alta ya realizada" (FR-9) si A consolida por fingerprint, o dos filas gemelas si no.
- **Sin dueño de las transiciones:** la Tabla de Convenciones dice "toda mutación pasa por servicios", pero no dice qué servicio escribe cada eje. Peor: AD-2/AD-4 fijan que el pase a `no_fiable` ocurre **cuando el apagado comprueba el fingerprint** — el servicio de status (AD-3) solo hace ICMP/ARP, **nunca re-verifica fingerprints**; así, una máquina con IP reasignada a un host distinto se muestra "online" indefinidamente y la UI ofrece Encender/Apagar con normalidad; el `no_fiable` solo se descubre en el primer intento de apagado (el momento más arriesgado, AD-2 "no se actúa sobre ella"). El estado que ve la app y el estado que aplica el BE divergen por diseño.
- **¿Lo arregla algún AD?** No. AD-3 y AD-5 no se cruzan: nadie declara la clave de upsert del inventario (MAC vs fingerprint vs IP) ni el dueño del flip `gestionada`; AD-3 no incluye verificación de fingerprint en el ciclo de estado.
- **Recomendación (AD nuevo):** **AD-11 — Escritores de estado**: una única regla "la fila de inventario tiene clave de identidad = `host_fingerprint` (o MAC si aún no se fijó), nunca IP"; el flip `gestionada` lo escribe exclusivamente `services/enrollment`; el servicio de status re-verifica el fingerprint en cada comprobación de una máquina gestionada (no solo ICMP/ARP): mismatch → `no_fiable` sin esperar a un shutdown; el alta sobre una fila ya gestionada → 409 (no fila nueva). Alternativa Deferred: consolidación de duplicados por fingerprint.

---

## F8 — [medium] El catálogo de errores fija códigos pero no sus significados por endpoint: 422 y 409 son ambiguos y el mapeo de la app no tiene dueño

- **Pareja:** BE (`api`, AD-1/FR-9) vs app (`data/remote`, AD-8; EXPERIENCE "errores con motivo humano").
- **Unidad A (cumple FR-9):** usa 422 para "cuerpo inválido, MAC inválida" *y*, en el alta, para "credenciales SSH inválidas" (FR-6); usa 409 para "alta ya realizada" *y* "escaneo en curso".
- **Unidad B (cumple AD-8 → EXPERIENCE y FR-15):** mapea 422 → "credenciales incorrectas" en el diálogo de alta; un 422 del wake (MAC malformada, p. ej. tras una MAC corrupta en el inventario) pinta el error de credenciales al usuario; un 409 de "escaneo en curso" en "escanear ahora" no tiene entrada en el mapeo de B (FR-15 solo define 401 y timeout) y cae en "Error de red" genérico o en `error` no tratado.
- **Colisión de contrato:** el mismo código con dos significados según endpoint exige que la app disambigüe por ruta — una convención cross-unidad que **ningún AD registra** (AD-1 solo impone "errores del catálogo FR-9"). Y el cuerpo uniforme `{"error":{"code","message"}}` (Tabla de Convenciones) no lleva un `code` por dominio ("invalid_credentials" vs "invalid_mac"), así que B no tiene a qué aferrarse.
- **¿Lo arregla algún AD?** No: AD-1 y AD-6 fijan códigos y límites, no la semántica por endpoint ni el mapeo app→motivo humano.
- **Recomendación (AD nuevo):** **AD-12 — Semántica de error del contrato**: tabla por endpoint (422 en `/enrollment` = credenciales; 422 en `/wake` = MAC; 409 en `/enrollment` = ya gestionada; 409 en `/scan` = escaneo en curso), y el `code` del cuerpo uniforme pasa a ser el identificador estable que la app mapea a strings (EXPERIENCE); mapeo 401/404/409/422/429 → estado UI definido en AD-10/AD-8; sin mapeo para 5xx (retry genérico).

---

## F9 — [medium] Envelope de arranque: nadie decide qué pasa si la interfaz tailnet aún no existe cuando el API intenta bindear

- **Pareja:** ops/despliegue (unidad A del sistema, "Entorno operacional") vs BE (`api`/uvicorn, AD-6).
- **Unidad A (cumple AD-6 "escucha solo en la interfaz de la tailnet" y el Entorno "Wants=network-online.target"):** el unit de systemd arranca el servicio tan pronto como hay red; uvicorn bindea a la **IP de tailnet** (p. ej. `100.x.y.z:8443`).
- **Unidad B (cumple AD-6 de la otra forma plausible):** bindea al **nombre de interfaz** (`tailscale0:8443`) o a una IP configurada en `config.toml [api]` que Tailscale no asignó en este arranque (o asignó distinta tras re-crear el nodo).
- **Colisión:** al boot de la RPi, `tailscaled` arranca *después* de `network-online.target`; en el instante del `bind`, la interfaz/IP no existe → `EADDRNOTAVAIL` → el proceso muere. Sin decisión binding, cada equipo resuelve distinto: A pone `Restart=always` con backoff y consigue bindear al reintentar; B declara `After=tailscaled.service` (que no garantiza la IP) o, peor, nadie lo declara y el servicio queda dead-en-boot — la app ve "BE inalcanzable" (FR-15). Adicional: si la IP static en config diverge de la IP real de tailnet (rekey/renodo), el BE queda inaccesible *para siempre* sin que ningún AD lo detecte.
- **Nota de diseño:** AD-6 dice "interfaz de la tailnet" pero uvicorn bindea a direcciones, no a interfaces; hay que traducir "interfaz" a IP y esa traducción es exactamente el hueco.
- **¿Lo arregla algún AD?** No. Ningún AD ni el Entorno Operacional decide `Restart=`, `After=`, retry/con backoff, ni cómo se resuelve la IP de escucha (dinámica vs estática).
- **Recomendación (AD nuevo o Deferred):** **AD-13 — Ops/envelope**: `Restart=on-failure` con `RestartSec` (backoff ante `EADDRNOTAVAIL`), `After=tailscaled.service network-online.target`, resolución de la IP de escucha dinámica (descubrir la IP de la interfaz tailnet en runtime, nunca IP estática en `[api]`), y `GET /status` reporta la dirección real de escucha para diagnóstico. Deferred: TLS/HTTPS (ya existe la entrada).

---

## F10 — [low] TTL/estado tras el alta: ventana donde la UI deshabilita acciones de una máquina recién gestionada

- **Pareja:** `services/enrollment` (AD-5) vs `services/status` (AD-3/FR-2).
- A escribe `gestionada=True` sin tocar reachability; B marca `offline` por TTL caducado durante el alta (el alta por SSH puede tardar > 30 s con TTL 60 s). Resultado: máquina recién gestionada durante 30–60 s "Apagada" → Apagar deshabilitado (FR-13) pese a estar online, y un Encender inútil. Menor, pero los dos equipos lo resolverán de forma distinta (A refrescando estado tras alta; B manteniendo TTL durante operaciones) si no se fija.
- **¿Lo arregla algún AD?** No; AD-5/AD-3 no definen quién refresca el estado al cerrar el alta.
- **Recomendación:** regla en AD-11 (escritores de estado): al completar alta, el servicio que muta `gestionada` refresca el estado de alcance de esa máquina (re-sonda dirigida) y el status service no pisa ese refresco en el siguiente tick. Baja prioridad; puede ir a Deferred.

---

## Resumen

| # | Severidad | Hallazgo | AD que lo arreglaría |
|---|---|---|---|
| F1 | critical | Identidad SSH de apagado: usuario, ruta/algoritmo de clave, formato/binding del fingerprint | AD-9 nuevo |
| F2 | high | `POST /scan`: cuerpo de respuesta y semántica de concurrencia (block vs 409) | AD-3 endurecido o AD nuevo |
| F3 | high | `no_fiable` ausente del DTO → UI ofrece Apagar (AD-2/AD-4) | AD-10 nuevo |
| F4 | high | Allowlist sudoers: comando canónico y config sin dueño | AD-4 endurecido |
| F5 | high | Token: pre-imagen del hash, sal, caso, contadores 429 | AD-6 endurecido |
| F6 | medium | Broadcast/interfaz WOL: derivar vs configurar; campo `wol_interface` | AD-4 endurecido |
| F7 | medium | Escritores de estado: flip gestionada, clave de upsert, `no_fiable` solo vía shutdown | AD-11 nuevo |
| F8 | medium | Semántica de errores por endpoint (422/409 ambiguos) y mapeo app sin dueño | AD-12 nuevo |
| F9 | medium | Envelope de arranque: bind a tailnet que aún no existe | AD-13 nuevo / Deferred |
| F10 | low | TTL tras el alta: ventana de acciones deshabilitadas | AD-11 (nota) / Deferred |
