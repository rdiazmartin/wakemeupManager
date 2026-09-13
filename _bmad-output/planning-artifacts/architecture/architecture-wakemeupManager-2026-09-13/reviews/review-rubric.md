# Review — Rubric walker · ARCHITECTURE-SPINE.md

**Doc:** `_bmad-output/planning-artifacts/architecture/architecture-wakemeupManager-2026-09-13/ARCHITECTURE-SPINE.md`
**Altitude:** feature · **Scope:** hexagonal Python BE (Raspberry Pi) + Android MVVM/UDF app; contrato único = API REST
**Date:** 2026-09-13

---

## Gate verdict

**CONDITIONAL PASS.** El spine es fuerte: los 15 FRs están vinculados a ADs, el envelope operacional existe (systemd, usuario dedicado, journald, backups documentados), Deferred está limpio, el mermaid es válido y el stack está pineado. Pero hay **3 fallos altos** en los dos puntos de divergencia más caros del sistema — el propio contrato REST (paths no pineados) y dos contradicciones con fuentes vinculadas (FR-6/sshpass y el DELETE del PRD vs. UX) que dejan a las dos unidades construyendo incompatibles o con alcance ambiguo. Requiere una pasada de finalización antes de epics.

---

## Checklist results

| # | Dim | Result |
|---|---|---|
| 1 | Seams para epics cubiertos | ⚠️ 3 high (paths del contrato, AD-5 vs FR-6, DELETE UI) |
| 2 | Reglas ejecutables y preventivas | ⚠️ AD-8 "estados conforme EXPERIENCE.md" es blanda; FR-5 `warning` sin regla |
| 3 | Deferred sin divergencia | ✅ Limpio; toda entrada es single-unit o v2 decidido |
| 4 | Envelope operacional | ⚠️ Deploy/backups/config sí; install/upgrade, restart policy, healthcheck, entornos silenciosos |
| 5 | Placeholders/diagramas/stack | ✅ Sin placeholders; 2 mermaid válidos; stack pineado (salvo uv) |
| 6 | FRs y spines UX | ✅ FR-1..15 todos bindeados (tabla abajo); popup apagado/polling/estados cubiertos por AD-8 |

---

## Findings

### [HIGH] F-1 — El contrato REST (la única seam entre unidades) no pinea paths ni cuerpos; epics pueden elegir spellinges incompatibles
- **Where:** AD-1 (§ L47) + Consistency Conventions "Naming" (§ 99); PRD FR-9
- **Detail:** AD-1 declara la API REST *el* contrato entre unidades pero solo referencia "el catálogo FR-9". De los ~6 endpoints, solo se pinea `POST /scan` (AD-3) y `POST /machines/{id}/wake` (AD-4). No existe ningún path canónico en todo el documento para shutdown, enrollment, consulta de escaneo ni el cuerpo del alta (`{user, password}`). Dos equipos independientes pueden elegir `POST /machines/{id}/shutdown` vs `/shutdown` vs `/machines/{id}/power-off` → 404 eterno hasta inspección manual.
- **Fix:** Anexar a AD-1 la lista verbatim de endpoints (método + path + body + errores por endpoint, p. ej. esquema OpenAPI) como parte del spine.

### [HIGH] F-2 — AD-5 contradice la consecuencia testeable del PRD FR-6 (sshpass vs asyncssh) sin nota de supersede
- **Where:** AD-5 (§ L71) vs PRD §4.3/FR-6 (líneas 160-161)
- **Detail:** el spine vincula FR-6 pero cambia el mecanismo de entrega de la password: AD-5 manda `asyncssh` en proceso "sin sshpass", mientras FR-6 exige "fichero temporal chmod 600 con O_EXCL, usado con `sshpass -f`, borrado tras el alta". El equipo de épicas que lea FR-6 construirá pruebas sobre sshpass (y verá fallar el comportamiento asyncssh), o entregará la password por argv/entorno que el propio spine prohíbe.
- **Fix:** Nota explícita de supersede en AD-5 ("reemplaza el mecanismo de FR-6: asyncssh en proceso, sin sshpass; la password llega por el body del alta vía API") y sincronizar el PRD FR-6.

### [HIGH] F-3 — DELETE de máquina: el PRD lo exige en la app, la UX lo banea, ningún AD lo decide
- **Where:** FR-3 (PRD §117-119: "solo desaparecen... mediante borrado explícito: `DELETE` vía API o diálogo de confirmación en la app") vs EXPERIENCE.md §68 ("Banned: swipe-to-delete... no existe DELETE en la UI de v1") vs AD-3 (§ L63: "solo `DELETE` explícito" sin decir quién lo invoca)
- **Detail:** el BE construirá el endpoint DELETE (si, y solo si, lo deduce); la app no sabe si debe construir el diálogo de confirmación de borrado — UX dice que no existe, PRD dice que sí. Divergencia de alcance real entre dos fuentes vinculadas que el spine no resuelve.
- **Fix:** Decidir en AD-8 (p. ej. "dialog de borrado con confirmación en la app, v1, conforme FR-3") y corregir la línea Banned de EXPERIENCE.md, o anotar "DELETE solo vía API/CLI, no en la UI".

### [MEDIUM] F-4 — Envelope operativo silencioso: instalación/upgrade, restart policy, healthcheck, entornos
- **Where:** § Entorno operacional (L151), AD-7 (§ L83-87)
- **Detail:** deployment está cubierto (systemd `Wants=network-online.target`, usuario `wakemeup`, rutas 600/700, journald, backups v1). Pero ninguna decisión cubre: cómo se instala/actualiza el artefacto en la Pi (uv? release? script?), política de reinicio (`Restart=on-failure`), healthcheck del servicio, ni entornos dev vs prod (paridad de config). La checklist exige que cada dimensión del envelope esté decidida, diferida o abierta — estas están silenciosas.
- **Fix:** Un bloque decisión en Entorno operacional: "install/upgrade v1 = script documentado con uv; systemd `Restart=on-failure`; healthcheck = `GET /status`; sin separación dev/prod en v1 (open question si se requiere)".

### [MEDIUM] F-5 — AD-8 gobierna los estados de UX con una cláusula genérica; FR-5 `warning` sin regla
- **Where:** AD-8 (§ L89-93), AD-4 (§ L65-69); EXPERIENCE.md §52-60; PRD FR-5 (L146)
- **Detail:** (a) La regla de AD-8 dice "estados conforme EXPERIENCE.md" sin nombrar los estados que no deben caerse en épicas: pantalla de primer arranque sin configurar, banner persistente de 401 con ajustes accesibles, lista cacheadada con badge "Sin conexión", estado `no_fiable` ámbar sin botón Apagar. Sin enumerarlos, una épica puede omitirlos sin violar ninguna regla concreta. (b) AD-4 vincula FR-5 pero su regla no menciona la consecuencia testeable de FR-5: `GET /status` debe exponer el nombre de la interfaz emisora y el BE debe reportar `warning` (WOL no fiable) en ausencia de Ethernet.
- **Fix:** Enumerar los 4-5 estados críticos de EXPERIENCE.md dentro de la regla AD-8 y añadir a AD-4 la regla de exposición de interfaz + estado `warning` de FR-5.

---

## Tail (low)

- **[low] § Stack (L113)** — "uv: actual estable en install" no pinea versión; los demás están pineados. Fijar versión uv en el pyproject/README del repositorio de épicas.
- **[low] AD-2 (L53-57)** — no se decide cómo se re-fija la MAC/fingerprint cuando el usuario reemplaza la NIC o re-instala el SO de la máquina (el fallo de fingerprint solo marca `no_fiable`, sin procedimiento de re-alta). Añadir una línea: "re-alta = volver a ejecutar enrollment (AD-5)".
- **[low] AD-6 (L77-81)** — el rate-limit dice "5 en 5 min" sin especificar el ámbito (por token, por IP, o ambos) que FR-10 sí especifica ("mismo token o dirección"). Alinear el texto.
- **[low] AD-7 (L83-87)** — "no se hacen backups de la clave privada": si la SD muere, todas las máquinas requieren re-alta; aceptable, pero la rotación manual diferida debería anotar que re-alta también. Nota de operaciones.

## Checks positivos

- **Deferred (L163-173):** las 9 entradas son single-unit o v2 decidido (TLS re-abrible, web reusa API, alertas baneadas por UX). Ninguna genera divergencia entre las dos unidades en v1.
- **Mermaid (L27-43):** ambos diagramas tienen sintaxis válida (labels con `<br/>`, edge labels con pipe, nodo stadium SQLite).
- **FR map** (§ AD Binds vs PRD §4):

| FR | Governed by | FR | Governed by |
|---|---|---|---|
| FR-1 | AD-2, AD-3 | FR-9 | AD-1, AD-6 |
| FR-2 | AD-3 | FR-10 | AD-6 |
| FR-3 | AD-3 (+AD-1) | FR-11 | AD-6, AD-7 |
| FR-4 | AD-4 | FR-12 | AD-3, AD-8 |
| FR-5 | AD-4 (⚠️ parcial, F-5) | FR-13 | AD-4, AD-8 |
| FR-6 | AD-2, AD-5 (⚠️ F-2) | FR-14 | AD-5, AD-8 |
| FR-7 | AD-2, AD-4 | FR-15 | AD-8 |
| FR-8 | AD-7 | — | — |

  Ningún FR sin decisión gobernante.
- **UX spine:** dialog de apagado (Flow 3) en AD-8 ✓ · polling 30s (10-300, pausable) ✓ · pull-to-refresh/escanear ahora ✓ · token Keystore+DataStore, EncryptedSharedPreferences excluida ✓ · strings ES/EN v1 ✓ · `no_fiable` coherente con estado ámbar de AD-2 ✓.
- **Consistencia interna:** TTL 60s / comprobación TTL/2 (AD-3 = FR-2) ✓ · singleflight escaneo periódico+forzado (AD-3 = FR-1/FR-3) ✓ · tokens 32 B hasheados, revocables (AD-6 = FR-10) ✓ · body de error `{"error":{code,message}}` ✓.
