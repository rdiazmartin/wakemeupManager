- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-esqueleto-del-be.md`
  summary: Definir política de exención de auth para el healthcheck GET /api/v1/status cuando llegue el middleware de tokens (story 1.4).
  evidence: FR-10 exige token en toda petición; el healthcheck es anónimo por utilidad operativa (systemd Restart/healthcheck). Decidir lista de exenciones explícita en 1.4.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-descubrimiento-de-maquinas.md`
  summary: Ruta del fichero SQLite CWD-relative: fijarla con config `[db]` e instalador (story 1.4/4.1).
  evidence: Db() usa Path("wakemeup.db") relativo al CWD; en despliegue systemd WorkingDirectory=/var/lib/wakemeup la DB caería ahí sin estar documentado. Decidir ruta + knob env en 1.4.
  resolution: RESUELTO 2026-09-15 (story 4.1) — el instalador escribe `[db] path = /var/lib/wakemeup/wakemeup.db` (ruta absoluta) y la unidad fija `Environment=WAKEMEUP_DB__PATH=/var/lib/wakemeup/wakemeup.db`; la DB nunca queda a merced del CWD.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-descubrimiento-de-maquinas.md`
  summary: `_in_flight` reporta False durante la fase de upsert de un escaneo en curso.
  evidence: verificado en revision 1.2 (verification-gap): _in_flight se limpia al terminar scan_range, antes del upsert; el endpoint POST /scan de la story 1.4 deberá consumir estado "scanning" real de la tarea _current en lugar de in_flight.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-2-descubrimiento-de-maquinas.md`
  summary: own_interfaces con rc!=0 (o timeout) devuelve set() silencioso: el BE podría no auto-excluirse.
  evidence: riesgo asumido y documentado; mitigación parcial (loopback/tailnet se excluyen por rango propio). Revisar con el instalador 4.1.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-primer-arranque-y-configuracion-de-conexion.md`
  summary: KeystoreSecretStore (producción, en cada arranque) sin ninguna ejecución automatizada: Robolectric no simula AndroidKeyStore.
  evidence: verification-gap 1.6: todos los tests usan dobles en memoria; la construcción de MasterKey/EncryptedSharedPreferences lanza AndroidKeyStore not found en JVM. Requiere verificación en dispositivo (primer arranque real + re-arranque con sesión persistida) o androidTest instrumentado.
  resolution: RESUELTO 2026-09-14 — verificado en dispositivo real (Samsung SM-T220, Android 14, APK debug del epic 2): primer arranque (FirstRunScreen), re-arranque con sesión reutilizada (va directa al listado, sin re-login) y 401 revocado (token revocado por CLI → polling recibe 401 → limpia config y redirige a Ajustes con campos vacíos). KeystoreSecretStore funciona en producción; no queda acción.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-epic-2-enciende-y-apaga.md`
  summary: El test Robolectric de no-persistencia de la password del alta solo aserta el cierre del diálogo y el body del POST; el almacén real (Keystore/EncryptedSharedPreferences) no se ejercita.
  evidence: Revisión del epic 2 (blind-hunter): un fallo de persistencia de la password en SecureSettingsRepository no se detectaría en JVM; requiere androidTest instrumentado o verificación en dispositivo (mismo deferred que KeystoreSecretStore de 1.6).
- source_spec: `_bmad-output/implementation-artifacts/spec-2-epic-2-enciende-y-apaga.md`
  summary: send_wol envía solo al broadcast global 255.255.255.255:9 sin fallback al broadcast dirigido de la subred física; redes que filtran el global harían que el wake "funcione" en falso.
  evidence: Revisión del epic 2 (blind-hunter): la nota de diseño 2.2 preveía el broadcast dirigido "si es determinable" y no se implementó; verificación en LAN real al desplegar.
- source_spec: `_bmad-output/implementation-artifacts/spec-2-epic-2-enciende-y-apaga.md`
  summary: Sin vía de recovery para no_fiable: re-enroll devuelve 409 y no hay CLI/API para re-fijar el fingerprint ni borrar la marca.
  evidence: Revisión del epic 2 (blind-hunter): el estado lo crea el propio sistema (AD-2) y su única salida es la edición manual de la DB; requiere diseño (CLI re-enroll o UI de re-alta) fuera del AC.
- source_spec: `_bmad-output/implementation-artifacts/spec-3-epic-3-mcp-y-eventos.md`
  summary: La notificación del fallback de polling usa el cuerpo genérico ("El agente realizó una acción") porque el BE solo expone `last_origin`, no la acción del último cambio.
  evidence: Revisión del epic 3 (blind-hunter): `notifyPolledMcpChanges` sintetiza `type="machine_change"` (→ `EventAction.UNKNOWN` → cuerpo genérico). Con el stream caído, un shutdown del agente notificaría "realizó una acción" en vez de "apagó la máquina". Fix completo = añadir `last_action`/tipo del último cambio al DTO de `GET /machines` (aditivo, AD-10) para conservar la semántica en el fallback.
- source_spec: `_bmad-output/implementation-artifacts/spec-3-epic-3-mcp-y-eventos.md`
  summary: El camino de `MachineListScreen` sin ViewModel construye el notificador pero no compone el colector de prompts de permiso, así que un prompt `POST_NOTIFICATIONS` se pierde en esa rama.
  evidence: Revisión del epic 3 (edge-case): `MachineListScreen.kt` fallback crea `AndroidMachineNotifier` sin `NotificationPermissionHost`; hoy no es alcanzable en producción (MainActivity siempre pasa ViewModel), pero es un footgun latente si se usa el fallback. Fix = factorizar el host de permiso para ambas vías o eliminar el fallback sin notificador.
- source_spec: `_bmad-output/implementation-artifacts/spec-3-epic-3-mcp-y-eventos.md`
  summary: El allowlist de `TransportSecuritySettings` solo admite hosts de `[api] bind_hosts` y loopback; los nombres MagicDNS de la tailnet no están contemplados.
  evidence: Revisión del epic 3 (blind-hunter/edge-case): el SDK valida el header `Host`; si un cliente MCP usa el nombre MagicDNS de la tailnet en lugar de la IP, se rechazaría. Se decide con el bind real de uvicorn en la story 4.1.
  resolution: RESUELTO 2026-09-15 (story 4.1, Q2=IMPLEMENTAR) — nueva clave `[api] extra_allowed_hosts` que se suma al allowlist de `_build_transport_security` además de `bind_hosts`/loopback; el instalador escribe el `DNSName` de tailscale detectado (`tailscale status --json`) y el test `test_transport_security_includes_extra_allowed_hosts` lo verifica.
