---
title: '1-6-primer-arranque-y-configuración-de-conexion'
type: 'feature'
created: '2026-09-14'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
context: []
baseline_commit: '09a9c73'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** La app (1.5) funciona con URL y token de ejemplo de `BuildConfig`; no hay pantalla de primer arranque ni forma de configurar el BE real, y el token no se guarda de forma segura.

**Approach:** Añadir primer arranque y ajustes bilingües (ES/EN): pantalla dedicada "Configurar" → ajustes URL+token con validación contra el BE (401 → "Token rechazado — revisa los ajustes", timeout → "BE inalcanzable"), almacenamiento seguro (Keystore + cifrado) sustituyendo la impl BuildConfig de `SettingsRepository` sin tocar el ViewModel del listado, sesión reutilizada salvo 401 revocado → redirige a ajustes. Cubierto con Robolectric.

## Boundaries & Constraints

**Always:**
- Stack fijado 1.5: Kotlin 2.4.20, Compose BOM 2026.09.00 (M3 1.4.0), AGP 9.4/Gradle 9.7, `security-crypto 1.1.0` + `datastore-preferences 1.2.1` (ya declaradas en `app/build.gradle.kts`).
- Arquitectura sin tocar capas ajenas: `SettingsRepository` (interfaz) mantiene `val apiUrl` / `val deviceToken` síncronos (WakemeupApi los lee como val); se EXTRAE la persistencia a una impl segura (`SecureSettingsRepository`) con escritura (`suspend fun save(...)` / `suspend fun clear()`) que sustituye a `InMemorySettingsRepository` solo en producción, sin modificar `WakemeupApi` ni `MachineListViewModel` (salvo el evento de 401 revocado descrito abajo).
- Token NUNCA en SharedPreferences planas ni en texto plano: Keystore (MasterKey AES256_GCM) + cifrado (EncryptedSharedPreferences o EncryptedFile+DataStore según lo que resuelva la impl — AD-8, FR-15). URL y token van en el mismo almacén seguro.
- Navegación sin librería (2 pantallas): `MainActivity` decide entre primer arranque / ajustes / listado según estado de configuración cargado y eventos de 401 (UDF con state simple + callbacks; Navigation Compose se deja para Epic 2 si se necesita).
- Validación al guardar (contrato y mensajes: AC 1.6 + UX-DR9) — **decisión del usuario (OQ-1): validar contra `GET /machines`** (únicamente endpoint autenticado del contrato 1.4; `/status` está exento de auth en el BE — AD-6 — y nunca devolvería 401): 401 → token rechazado (limpiar pantalla de errores, NO guardar); timeout/red → BE inalcanzable (NO guardar); 200 → guardar y navegar al listado. Sin cambios de contrato ni del BE.
- 401 revocado en sesión activa: `MachineListViewModel` distingue `ApiException.code == "unauthorized"` (envelope 1.4 `{error:{code}}`) del resto → emite evento de sesión inválida; `MainActivity` limpia la configuración (vuelta a ajustes/primera configuración). El resto de fallos mantienen el badge "Sin conexión" actual.
- Primer arranque UX-DR4: explicación breve sin tecnicismos + botón "Configurar"; sin lista fantasma. Desde el listado, el ajuste es accesible (banner/acción de configuración en el header cuando la sesión es válida pero hay token inválido → "Token rechazado — revisa ajustes", EXPERIENCE.md).
- URL del campo de ajustes: **decisión del usuario (OQ-2): se pide la URL BASE del BE** (ej. `http://10.0.0.1:8000`); la app la normaliza (quita `/` final, añade `/api/v1` si no termina en él) y rechaza formatos no-`http(s)` con error local. La URL que se guarda es la base normalizada + `/api/v1`.
- Strings ES/EN externas (`res/values/` + `res/values-en/`) para TODA la UI nueva (NFR-9); targets táctiles ≥48dp, TalkBack etiquetado, Dynamic Type sin truncar.
- `ComponentActivity` en `src/debug/AndroidManifest.xml` se MANTIENE (aún cubre los tests de Compose con `createComposeRule()`; verificado en 1.5).
- Robolectric: el cifrado se diseña con el almacén inyectable (`MasterKey`/prefs) para permitir tests; si el Keystore simulado de Robolectric no soporta la impl real, los tests de comportamiento usan el doble inyectado y la impl real se verifica por construcción + nota en Implementation Notes (nunca un test verdea "de mentira").
- Tests Robolectric exigidos (AC): validación 401 y timeout al guardar, almacenamiento seguro (el token no aparece fuera del almacén encriptado), reutilización de sesión (config guardada → arranque directo al listado) y 401 revocado → redirección a ajustes.
- Commit + push al finalizar (requisito transversal).

**Never:**
- No se toca el BE (contrato 1.4 verbatim; la validación usa `GET /machines` autenticado, sin cambios de contrato).
- No se toca `WakemeupApi` (salvo si la OQ-1 exige un endpoint nuevo de validación) ni la firma de `SettingsRepository` (interfaz) ni el ViewModel de listado salvo el evento 401 ya descrito.
- No se usa `EncryptedSharedPreferences` si se confirma su deprecación en la versión instalada (verificada en impl: javap de 1.1.0 lo muestra sin `@Deprecated`; si apareciera, se usa EncryptedFile+DataStore).
- No se persiste nada más que URL+token en esta story (idioma, opciones, etc. → epic futuro).
- No se añade librería de navegación ni DI framework.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Primer arranque sin config | app recién instalada | pantalla FirstRun: explicación breve + botón "Configurar" (UX-DR4) | — |
| Guardar válido | URL+token correctos, BE responde OK | valida OK → persiste cifrado → navega al listado | — |
| Guardar con token inválido | BE responde 401 | NO guarda; error visible "Token rechazado — revisa los ajustes" | se mantiene en ajustes |
| Guardar con BE inalcanzable | timeout / host no resuelve / conexión rechazada | NO guarda; "BE inalcanzable" | se mantiene en ajustes |
| URL inválida | cadena sin `http(s)://` o vacía | error local de formato antes de llamar al BE | no se llama al BE |
| URL con `/` final o sin `/api/v1` | `http://h:8000/` / `http://h:8000` | normaliza a `http://h:8000/api/v1` | — |
| Arranque con sesión guardada | config persistida, BE OK | va directo al listado (sin FirstRun) | — |
| 401 revocado en sesión activa | `GET /machines` → 401 | evento sesión inválida → limpia config → redirige a ajustes | sin badge "Sin conexión" (es 401, no red) |
| Falla de red en sesión activa | timeout en listado | badge "Sin conexión" (comportamiento 1.5 intacto) | — |
| Guardar en curso (doble tap) | dos guards simultáneos | solo una validación/escritura en vuelo (guard) | segunda tap ignorada |
| Ajustes accesibles desde listado | token válido pero usuario quiere cambiarlo | acción de ajustes en el header; banner persistente en 401 (EXPERIENCE.md) | — |

</frozen-after-approval>

## Code Map

- `android/app/src/main/java/com/wakemeup/manager/data/local/SettingsRepository.kt` -- interfaz actual (`val apiUrl`, `val deviceToken`); se amplía con `suspend fun save(apiUrl: String, token: String)` / `suspend fun clear()` + `suspend fun load()` (o equivalente de inicialización) MANTENIENDO los `val` síncronos.
- `android/app/src/main/java/com/wakemeup/manager/data/local/InMemorySettingsRepository.kt` -- impl BuildConfig 1.5; queda solo para tests (los tests ya la inyectan). Production deja de usarla.
- `android/app/src/main/java/com/wakemeup/manager/data/local/SecureSettingsRepository.kt` (NUEVO) -- impl segura: MasterKey (Keystore, AES256_GCM, sin auth de usuario en v1 para evitar lockscreen) + almacén cifrado; caché en memoria para `val apiUrl/deviceToken`; construida con almacén inyectable para tests.
- `android/app/src/main/java/com/wakemeup/manager/data/local/SecretStore.kt` (NUEVO, interfaz) -- `getString/putString/remove/hasConfig` sobre el almacén cifrado; impl real sobre Keystore; doble de test sin Keystore.
- `android/app/src/main/java/com/wakemeup/manager/data/remote/WakemeupApi.kt` -- ya lanza `ApiException(code="unauthorized", ...)` para 401 (envelope 1.4). Se añade `suspend fun validateConnection(): Unit` que llama a `GET /machines` (decisión OQ-1) y reutiliza el 401 mapping existente; timeout/excepción de red se propagan para que el ViewModel distinga "BE inalcanzable".
- `android/app/src/main/java/com/wakemeup/manager/ui/machines/MachineListViewModel.kt` -- ÚNICO cambio permitido: emitir evento de sesión inválida cuando `ApiException.code == "unauthorized"` (hoy el fallo se trata como offline genérico en `refresh`/`scanNow`). El `createFactory()` pasa a construir `SecureSettingsRepository(context)` (necesita Context — revisar firma del Factory). Incluir el estado de "configurado" solo si participa del flujo de arranque (si no, lo decide MainActivity).
- `android/app/src/main/java/com/wakemeup/manager/ui/setup/FirstRunScreen.kt` (NUEVO) -- pantalla de primer arranque UX-DR4: explicación + botón "Configurar".
- `android/app/src/main/java/com/wakemeup/manager/ui/setup/SettingsScreen.kt` + `SettingsViewModel.kt` (NUEVOS) -- formulario URL+token, validación local de URL, guardado con validación remota (OQ-1), estados: `idle/saving/error(token|be|formato)/saved`, UDF, targets ≥48dp, TalkBack, campos tipo password con "mostrar" para el token.
- `android/app/src/main/java/com/wakemeup/manager/MainActivity.kt` -- pasa a enrutar (UDF en memoria): FirstRun → Settings → MachineList según config cargada + eventos 401; `setContent` con `WakemeupTheme`.
- `android/app/src/main/res/values/strings.xml` + `values-en/strings.xml` -- bloque "Primer arranque / Ajustes" completo ES/EN (explicación, Configurar, URL, token, guardar, "Token rechazado — revisa los ajustes", "BE inalcanzable", error de formato, ajustes desde el listado, banner 401).
- `android/app/src/test/java/com/wakemeup/manager/ui/setup/SettingsViewModelTest.kt` (NUEVO, Robolectric) -- validación contra MockEngine: 401 → `error(token)`, timeout/excepción de red → `error(be)`, OK → `saved`; URL inválida → `error(formato)` sin llamar al BE; doble guard → 1 llamada; normalización de URL.
- `android/app/src/test/java/com/wakemeup/manager/data/local/SecureSettingsRepositoryTest.kt` (NUEVO, Robolectric) -- token persiste y se lee (impl real si el Keystore de Robolectric lo soporta; si no, con SecretStore de test y nota), `clear()` deja sin config, `save/load` round-trip, y el token NO aparece en SharedPreferences planas del contexto (assert de no-plano).
- `android/app/src/test/java/com/wakemeup/manager/ui/machines/MachineListViewModelTest.kt` -- añadir: 401 en fetch → evento sesión inválida (no badge offline).
- `android/app/src/test/java/com/wakemeup/manager/MainActivityTest.kt` (NUEVO, Robolectric, Compose) -- reutilización de sesión (config guardada → lista; sin config → FirstRun), navegación FirstRun→Settings→Listado tras guardado OK, redirección a ajustes tras 401 simulado. (Cubre el gap de pantalla completa de 1.5: `viewModel()`/`viewModelFactory` reales vía factory con fakes inyectados.)

## Tasks & Acceptance

**Execution:**
- [x] `android/app/src/main/java/com/wakemeup/manager/data/local/SecretStore.kt` + `SecureSettingsRepository.kt` -- impl segura (Keystore+cifrado) con escritura/lectura/clear y caché para los `val`; almacén inyectable.
- [x] `android/app/src/main/java/com/wakemeup/manager/data/local/SettingsRepository.kt` -- ampliar interfaz (save/clear/load) sin romper `WakemeupApi` (los `val` se mantienen).
- [x] `android/app/src/main/java/com/wakemeup/manager/data/remote/WakemeupApi.kt` -- `validateConnection()` sobre `GET /machines` (decisión OQ-1), reutilizando el 401 mapping existente.
- [x] `android/app/src/main/java/com/wakemeup/manager/ui/setup/FirstRunScreen.kt` + `SettingsScreen.kt` + `SettingsViewModel.kt` -- UDF bilingüe, errores UX-DR9, guard + normalización de URL.
- [x] `android/app/src/main/java/com/wakemeup/manager/MainActivity.kt` -- enrutado UDF FirstRun/Settings/Listado + escucha de eventos 401 (limpiar → ajustes); factory de listado con `SecureSettingsRepository`.
- [x] `android/app/src/main/java/com/wakemeup/manager/ui/machines/MachineListViewModel.kt` -- evento de sesión inválida (401) separado de offline.
- [x] `android/app/src/main/res/values/strings.xml` + `values-en/strings.xml` -- strings ES/EN.
- [x] Tests Robolectric (3 archivos nuevos + extensión del de ViewModel) -- ver I/O Matrix + AC.
- [x] `.gitignore`/arbefactos: verificar que no entra nada de build (1.5 ya lo dejó).
- [ ] Commit + push al finalizar (pendiente de aprobación de paso 3 → commit del flujo).

**Acceptance Criteria:**
- Given la app recién instalada sin configurar, when se abre, then aparece la pantalla de primer arranque (UX-DR4) con explicación breve y botón "Configurar".
- Given los ajustes abiertos, when se guardan URL y token, then se valida contra `GET /machines` (decisión OQ-1): 401 → "Token rechazado — revisa los ajustes" y no guarda; timeout → "BE inalcanzable" y no guarda; OK → guarda y navega al listado (FR-15, UX-DR9).
- Given una sesión guardada, when la app se reabre, then va directo al listado (reutiliza sesión, FR-15); solo un 401 revocado redirige a ajustes (limpieza de config).
- Given el listado con sesión, when el BE responde 401, then se limpia la config y se redirige a ajustes (no muestra badge "Sin conexión").
- Given el token guardado, then el token y la URL viven en el almacén seguro (Keystore/cifrado) y NO aparecen en SharedPreferences planas (FR-15, AD-8, NFR-7).
- Given la UI de ajustes, then todos los textos están externalizados ES/EN (NFR-9), targets ≥48dp y campos/etiquetas TalkBack.
- Given las ACs previas, then existe cobertura Robolectric de: validación 401/timeout, almacenamiento seguro y reutilización de sesión (y el gap de pantalla completa documentado en 1.5) — requisito transversal.
- Given la story completada, then commit + push — requisito transversal del usuario.

## Implementation Notes

- **EncryptedSharedPreferences (no deprecada en security-crypto 1.1.0)**: verificado con `javap` — la clase NO tiene `@Deprecated` en la versión declarada (1.1.0). Se usa la vía canónica `MasterKey(AES256_GCM) + EncryptedSharedPreferences(AES256_SIV/AES256_GCM)` en `KeystoreSecretStore`; si una versión futura la deprecara, migrar a EncryptedFile+DataStore sin tocar el contrato `SecretStore` (nota en el archivo).
- **Scheduler de kotlinx-coroutines-test**: `SettingsViewModel` (como `MachineListViewModel` en 1.5) usa `viewModelScope` en producción y un `externalScope` inyectable; los tests inyectan `backgroundScope` con el scheduler virtual (`runCurrent()`; nunca `advanceUntilIdle`).
- **Robolectric y Compose**: `createComposeRule()` resuelve `androidx.activity.ComponentActivity` declarada en `src/debug/AndroidManifest.xml` (mantenida de 1.5 — sigue siendo necesaria).
- **Qualifiers `es` en MainActivityTest**: Robolectric usa `en_US` por defecto; el test de pantalla completa fija `@Config(qualifiers = "es")` para hacer assert de strings ES (patrón ya usado por MachineRowTest).
- **Carga de sesión antes de componer**: `AppRoot` lee `settings.load()` en `LaunchedEffect` y mantiene `startup: StartupState?` (null = cargando); listado y ajustes no se componen hasta resolver, para que los ViewModels nazcan con URL/token reales (la lectura del almacén cifrado es <1 ms; sin splash).
- **Recreación de ViewModels al cambiar de sesión**: `viewModel(key = "machines-$url-$token")` y `rememberApi` (key en `apiUrl`+`deviceToken`) — al guardar/limpiar la configuración se recrean api y ViewModels; el `LaunchedEffect(machineViewModel)` re-colecta `sessionInvalid` sobre la instancia nueva.
- **Matriz I/O cubierta en tests** (41 verdes: 24 de 1.5 + 17 nuevos): validación 401/timeout/formato, normalización URL (base/slash/idempotente/https/inalid), round-trip + clear del almacén, doble-tap guard (1 llamada BE), 401 no-offline en listado, pantalla completa (FirstRun→Settings→Listado, sesión directa, 401→ajustes con limpieza).
- **Bug encontrado en la normalización**: `substringBefore('/', "")` devuelve `""` si no hay `/` (negaba hosts válidos, ej. `http://h:8000`) → corregido a `substringBefore('/')` (devuelve la cadena completa si el delimitador no aparece); mismo fix en `defaultNormalizer` del ViewModel.
- **`createForTest` del SettingsViewModel**: solo para tests (inyecta scope y normalizador); la producción usa el constructor/factory estándar.

## Spec Change Log

## Review Triage Log

- verification-gap + edge-case-hunter `validateConnection lee la caché vacía del repo, no los valores del formulario` — **high** — verificado: `save()` validaba vía `api.validateConnection()` (que lee `settings.apiUrl` de la caché de `SecureSettingsRepository`, vacía en primer arranque/post-401); MockEngine matchea `endsWith("/machines")` y enmascaraba la petición relativa. **patch**: `validateConnection(apiUrl, deviceToken)` con la URL normalizada + token del formulario; tests endurecidos (assert de host/token en el handler).
- blind-hunter + edge-case-hunter `401 perdible (SharedFlow tryEmit extraBufferCapacity=1) → skeleton eterno o redirección perdida` — **high** — **patch**: `MutableSharedFlow(replay = 1)`; `isRefreshing=false` en la rama 401; tests de 401 en fetch y scan siguen verdes.
- blind-hunter + edge-case-hunter `ViewModel zombie: polling sigue tras navegar a ajustes/limpiar sesión` — **medium** — **patch**: `stop()` en `MachineListViewModel` + `DisposableEffect(onDispose)` en `AppRoot`.
- blind-hunter + edge-case-hunter `onBack de ajustes siempre va a FIRST_RUN (también entrando desde listado)` — **medium** — **patch**: `settingsOrigin` (rememberSaveable) distinguido por origen; test `ajustes accesibles desde el listado` + vuelta al listado.
- blind-hunter `_saved=true re-jugado reabre ajustes → auto-navega con las mismas credenciales` — **medium** — **patch**: `resetSaved()` en la entrada de pantalla (LaunchedEffect).
- blind-hunter `normalización duplicada byte-a-byte en 2 sitios` — **medium** — **patch**: `UrlNormalizer` único (con prefijo `/api/v1` idempotente en cualquier caja); ambos delegados apuntan a él; tests de normalización ampliados.
- blind-hunter `putString apply() → muerte de proceso pierde la escritura reciente` — **medium** — **patch**: `commit()` síncrono en `KeystoreSecretStore` (escritura crítica de token).
- blind-hunter `fallback de MachineListScreen construye KeystoreSecretStore en cada recomposición` — **medium** — **patch**: rama guardada en `remember` y solo compuesta con `viewModel == null` (en tests el Keystore de Robolectric lanza `AndroidKeyStore not found` → la rama no debe evaluarse al inyectar).
- blind-hunter `catch(_: Exception) traga CancellationException` — **medium** — **patch**: rethrow explícito de `CancellationException` en `save()`.
- blind-hunter `puerto no numérico / caso /API/v1 no idempotente` — **low** — rechazado con refutación parcial: prefijo `/api/v1` ahora idempotente en cualquier caja (UrlNormalizer); validación de puertos no numéricos se deja al transporte (mensaje "BE inalcanzable" correcto); no es un fallo cotidiano.
- blind-hunter `4xx/5xx del BE se pintan como "BE inalcanzable"` — **low** — rechazado: la superficie de mensajes la fijan spec+UX-DR9 (`Token rechazado — revisa los ajustes` para 401 y `BE inalcanzable` para el resto); un 500 no es un caso cotidiano de ajustes; no guarda nunca (comportamiento correcto).
- blind-hunter `dobles de SecretStore duplicados en 3 archivos de test` — **low** — rechazado: duplicación local acotada en tests (patrón existente en el repo); un fixture compartido añadiría indirección sin beneficio medible aún.
- blind-hunter `falta test de que el VM del listado se recrea contra la nueva URL/token` — **medium** — **patch parcial**: la recreación por key está cubierta de forma indirecta por `guardar con exito navega al listado` + `con configuracion guardada va directo al listado` (assert del estado del store); se documenta en Implementation Notes.
- verification-gap Gap 2 `rama else de ApiException no-401 sin test` — **medium** — **patch**: test `error HTTP no 401 tampoco guarda` (500 + envelope → `backendUnreachable`, sin persistenci).
- verification-gap Gap 3 + blind-hunter `KeystoreSecretStore sin ejecución (producción en cada arranque)` — **defer**: no cubierto por Robolectric (AndroidKeyStore no disponible en JVM); requiere verificación en dispositivo — véase deferred-work.
- verification-gap `edit de credenciales sobre sesión existente no cubierto` — **medium** — **patch parcial**: el fix del Gap 1 (validación con valores introducidos) cubre el caso; flujo de edición sobre sesión existente no tiene test dedicado — documentado en Implementation Notes.

## Design Notes

Para que `WakemeupApi` y `MachineListViewModel` no cambien su firma, `SecureSettingsRepository` actúa como proxy síncrono con caché en memoria: `MainActivity` (o el punto de carga) ejecuta `load()` una vez al arranque (DataStore/almacén cifrado responden en ms, sin splash perceptible); los `val apiUrl/deviceToken` devuelven la caché. El almacén se abstrae tras la interfaz `SecretStore` para que el test de comportamiento no dependa de que Robolectric simule el Keystore; si Robolectric soporta la impl real, el test la usa directamente (preferido).

## Verification

**Commands:**
- `./gradlew :app:compileDebugKotlin` -- expected: compila sin errores
- `./gradlew :app:testDebugUnitTest` -- expected: todos los tests Robolectric verdes (nuevos + 24 de 1.5 sin romper)
- `./gradlew :app:assembleDebug` -- expected: APK generado

**Manual checks (si el Keystore de Robolectric no cubre la impl real):**
- Inspeccionar que la impl real usa unicamente MasterKey/almacén cifrado (sin SharedPreferences planas) y que `values-en/strings.xml` está completo.
