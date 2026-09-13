---
title: '1-5-listado-de-máquinas-en-la-app-tema-oscuro-poll-y-escanear-ah'
type: 'feature'
created: '2026-09-13'
status: 'done'
route: 'dispatch'
review_loop_iteration: 0
context: []
baseline_commit: '89a6e10'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** El BE ya expone el inventario (1.4) pero no existe cliente: no hay proyecto Android, ni listado, ni tema, ni conexión con el BE.

**Approach:** Crear el proyecto Android (Kotlin/Compose, MVVM+UDF, paquetes `data/remote`, `data/local`, `domain`, `ui`) y la pantalla de listado de máquinas conforme UX-DR1/DR2/DR4/DR5 (AD-8): Machine row punto 10dp+meta, tema oscuro Material 3 + Dynamic Color con fallback a la paleta grafito de DESIGN.md, polling 30 s (10–300 s, pausable en ahorro de batería), pull-to-refresh, botón "escanear ahora" (POST /scan), skeleton 4–6 filas, lista vacía con acción, caché + badge "Sin conexión", strings ES/EN. Testeado con Robolectric: ViewModel (poll, escanear ahora, caché off-line) y la fila según estados.

## Boundaries & Constraints

**Always:**
- Stack del spine (decisión usuario): Kotlin 2.4.20, Compose BOM 2026.09.00 (M3 1.4.0), AGP 9.4.x/Gradle 9.7.x, targetSdk 36/minSdk 26, Ktor client 3.5.2 + kotlinx.serialization, security-crypto 1.1/DataStore 1.2.1 declaradas ya (el almacenamiento real de token llega en 1.6).
- Arquitectura: ViewModel + StateFlow + `collectAsStateWithLifecycle`; sin lógica de negocio en UI (AD-8); paquetes por feature.
- URL+token en 1.5 (decisión usuario): `SettingsRepository` (interfaz) con implementación en memoria alimentada por `BuildConfig` (URL y token de ejemplo); 1.6 conecta la interfaz a Keystore+Encrypted DataStore sin tocar el ViewModel.
- Acciones inline en 1.5 (decisión usuario): los botones (Dar de alta/Encender/Apagar) se renderizan conforme a UX-DR2 según estado, pero su tap no tiene efecto (lógica del Epic 2); TalkBack los anuncia como presentes; nunca navegan ni llaman al BE.
- Machine row conforme UX-DR2/DR5: punto 10dp + texto meta (SIEMPRE punto+texto, nunca solo color), nombre en título, IP/MAC en meta monoespaciada, acciones inline según estado: descubierta → "Dar de alta"; gestionada/offline → "Encender"; gestionada/online → "Apagar"; no_fiable → badge ámbar y sin acciones destructivas; fila offline con menos contraste.
- Listado conforme UX-DR4: polling 30 s (10–300 s, pausable en ahorro de batería), pull-to-refresh, "escanear ahora" en header (spinner mientras escanea; con Reduce Motion → texto "Escaneando…"), skeleton 4–6 filas en el primer fetch, lista vacía "No se encontraron máquinas" + acción de escaneo, fallos de conexión → lista cacheada + badge "Sin conexión" sin romper contenido.
- Tema oscuro por defecto: Material 3 + Dynamic Color (Material You) y fallback a la paleta grafito/acento (surface-base #121417, surface-raised #1A1D21, ink-primary #E6E8EB, ink-secondary #9BA4AE, success #4CC38A, warning #F2B24C, danger #F26D6D, accent #2DE0A5). Sin degradados ni brillos; estado nunca solo por color.
- Accesibilidad: targets táctiles ≥48dp, TalkBack anuncia nombre/estado/acciones por fila, filas ≥72dp, margen 16dp, Dynamic Type sin truncar.
- Strings ES/EN externas desde v1 (`res/values/` + `res/values-en/`).
- El DTO consume el contrato de 1.4 verbatim: `GET /api/v1/machines` → `{"machines": [{id,name,ip,mac,hostname,status,managed}]}`; `POST /api/v1/scan` → 202 `{scan:{running,triggered,discovered,duration_ms}}`; envelope `{error:{code,message}}`.
- Las 3 acciones de estado (Dar de alta, Encender, Apagar) se RENDERIZAN en la fila según estado — pero su lógica/es el Epic 2: en 1.5 el tap no hace nada (o muestra snackbar informativo); nunca navegan ni llaman al BE.

**Never:**
- No se implementa la pantalla de ajustes/primer arranque ni el almacenamiento del token (Keystore/DataStore) — story 1.6.
- No se implementa alta/encender/apagar vía API (Epic 2).
- No se consume SSE ni eventos (Epic 3).
- No se usa SharedPreferences ni se hace persistencia del token en esta story.
- No se toca el backend (contrato fijado en 1.4, verbatim).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Primer fetch OK | GET /machines 200 con 2 máquinas | skeleton → 2 filas con punto+texto, IP/MAC mono, acciones según estado | — |
| Fetch falla (red) | GET /machines timeout/error | lista cacheada + badge "Sin conexión" (contenido intacto) | no rompe la lista |
| Fetch con lista vacía | 200 `{"machines": []}` | empty state "No se encontraron máquinas" + acción escanear | — |
| Escanear ahora OK | POST /scan 202 {running:false,…} | spinner durante escaneo, lista refrescada tras 202 | — |
| Escanear con escaneo en curso | 202 {running:true} | spinner, lista refrescada igualmente (polling natural, AD-3) | — |
| Polling 30 s | tick del temporizador UDF | re-fetch silencioso que actualiza la lista | fallo de poll → badge/caché, sin crash |
| Ahorro de batería | evento de batería bajo | polling pausado (retoma al restaurar) | — |
| Fila online | managed=false, status=online | punto success + texto "Encendida" (o EN), acciones: sin "Encender" (inactiva en online) | — |
| Fila offline | status=offline | punto gris/off + texto, menos contraste, acción "Encender" | — |
| Fila no_fiable | status=no_fiable | badge ámbar "No fiable" sin acciones destructivas | — |
| Reduce Motion | ajuste del sistema | spinner sustituido por texto "Escaneando…" | — |

## Code Map

- No existe `android/` aún (el repo solo tiene `backend/`): la story crea el proyecto completo desde cero bajo `android/`.
- Contrato consumido (spec 1.4, `backend/src/wakemeup/api/__init__.py`): `GET /api/v1/machines` → `{"machines": [{id,name,ip,mac,hostname,status,managed}]}` (DTO AD-10, `status ∈ {online,offline,no_fiable}`, `managed=false` en v1) y `POST /api/v1/scan` → 202 `{"scan":{"running":bool,"triggered":bool,"discovered":int,"duration_ms":int}}`; envelope `{"error":{"code","message"}}` para errores.
- Paleta: DESIGN.md §Colors (`#121417` surface-base, `#1A1D21` raised, `#E6E8EB` ink-primary, `#9BA4AE` ink-secondary, `#4CC38A` success, `#F2B24C` warning, `#F26D6D` danger, `#2DE0A5` accent).
- UX: DESIGN.md §Components (Machine row), §State Patterns (skeleton, empty, offline), §Interaction Primitives (pull-to-refresh, scan button); EXPERIENCE.md §Accessibility Floor (TalkBack, ≥48dp, Reduce Motion).
- Stack fijado: epics.md línea 56 y ARCHITECTURE-SPINE.md §Stack.

## Tasks & Acceptance

**Execution:**
- [x] `android/settings.gradle.kts` + `android/build.gradle.kts` + `android/gradle.properties` + wrapper — proyecto Gradle/AGP raíz (stack según decisión del usuario).
- [x] `android/app/build.gradle.kts` — módulo app: Compose, Material 3, Ktor client core, kotlinx-serialization, lifecycle-viewmodel-compose, activity-compose; deps de test Robolectric.
- [x] `android/app/src/main/AndroidManifest.xml` — launcher de la actividad única de listado.
- [x] `android/app/src/main/java/.../domain/models.kt` — `Machine` (id, name, ip, mac?, hostname?, status enum online/offline/no_fiable, managed) y `ScanResult`.
- [x] `android/app/src/main/java/.../data/remote/WakemeupApi.kt` + `MachinesDto.kt` — cliente Ktor (URL+token del SettingsRepository), GET /machines, POST /scan, DTO del contrato 1.4 verbatim.
- [x] `android/app/src/main/java/.../data/local/SettingsRepository.kt` (interfaz + impl en memoria desde `BuildConfig` — decisión usuario) + `MachinesCache.kt` (caché en memoria de la última lista OK).
- [x] `android/app/src/main/java/.../ui/theme/` — tema oscuro M3 + Dynamic Color (fallback paleta grafito) con tokens DESIGN.md.
- [x] `android/app/src/main/java/.../ui/machines/MachineListScreen.kt` + `MachineRow.kt` + `MachineListViewModel.kt` — UDF: StateFlow con estado único (carga/skeleton, lista, vacía, sin conexión, escaneando), polling 30 s pausable con ahorro de batería, pull-to-refresh, escanear ahora, row conforme UX-DR2/DR5 (acciones renderizadas sin lógica — decisión usuario).
- [x] `android/app/src/main/res/values/strings.xml` + `values-en/strings.xml` — UI bilingüe (ES/EN) desde v1.
- [x] `android/app/src/test/.../MachineListViewModelTest.kt` (Robolectric) — poll, escanear ahora, caché off-line, empty state.
- [x] `android/app/src/test/.../MachineRowTest.kt` (Robolectric) — fila para online/offline/no_fiable/descubierta: punto+texto, metadatos, acciones por estado.

**Acceptance Criteria:**
- Given la app construida con URL+token configurados, when se abre el listado, then se muestran las Machine rows conforme UX-DR2 (punto 10dp + meta nunca solo color, nombre en título, IP/MAC monoespaciada, acciones inline por estado).
- Given un fetch con error de red, when la lista ya tenía contenido, then se muestra la lista cacheada con badge "Sin conexión" sin romper el contenido (UX-DR4).
- Given un fetch sin máquinas, when se abre la pantalla, then se muestra "No se encontraron máquinas" con acción de escaneo (UX-DR4).
- Given el listado cargado, when se pulsa "escanear ahora", then se dispara POST /scan (spinner durante el escaneo; texto "Escaneando…" con Reduce Motion) y la lista se refresca.
- Given el listado cargado, when pasan 30 s, then el UI se refresca silenciosamente (polling FR-12); con ahorro de batería el polling se pausa.
- Given una fila con status no_fiable, then muestra badge ámbar "No fiable" sin acciones destructivas (AD-10).
- Given el tema del sistema oscuro/material-you, then la app usa Material 3 + Dynamic Color con fallback a la paleta grafito (UX-DR1); las superficies/aceentos se aplican sin degradados.
- Given la UI, then todos los textos están en strings ES + EN externas (values/values-en) y los targets táctiles son ≥48dp.
- Given las ACs previas, then existe cobertura Robolectric del ViewModel (poll, escanear ahora, caché off-line, empty) y de la fila por estados — requisito transversal de tests.
- Given la story completada, then commit + push — requisito transversal del usuario.

**Implementation Notes**

- **compileSdk 37**: el stack del spine fija Compose BOM 2026.09.00 (M3 1.4.0 / Compose UI 1.12.1), cuyos AAR exigen compilar contra API 37 (`checkDebugAarMetadata` falla con 36). Instalada `platforms;android-37.0` + `build-tools;37.0.0` en el SDK del host; `targetSdk` sigue en 36 conforme al stack.
- **Built-in Kotlin de AGP 9.0**: el plugin `kotlin-android` no se aplica (incompatible con el DSL nuevo); solo se aplican `org.jetbrains.kotlin.plugin.compose` y `plugin.serialization`. KGP 2.4.20 se fija vía `buildscript`. La propiedad global `android.defaults.buildfeatures.buildconfig` ya no existe en AGP 9 → se usa `buildFeatures { buildConfig = true }`.
- **Robolectric + Compose**: `createComposeRule()` lanza `androidx.activity.ComponentActivity`; debe declararse en el manifest de la variante de test (`app/src/debug/AndroidManifest.xml`, referencia robolectric PR #4736).
- **kotlinx-coroutines-test 1.11 en este host (peculiaridad del scheduler)**: `advanceUntilIdle()` no ejecuta coroutines de `backgroundScope` ni tareas programadas a tiempo 0; `runCurrent()` sí. Con el polling `while(true)` activo, `advanceUntilIdle()` desborda el heap (el scheduler nunca alcanza idle). Receta usada: MockEngine con `dispatcher = StandardTestDispatcher(testScheduler)` y solo `runCurrent()` / `advanceTimeBy(interval)` + `runCurrent()`.
- **API Ktor 3.5.2**: `HttpStatusCode.isSuccess` (Ktor 2.x) ya no existe → helper local; `Context.registerReceiver(intent, flag)` roto con `Intent` → `IntentFilter`; un content lambda `@Composable` reutilizado en dos botones rompe el compilador → duplicado.
- **Acciones de fila**: los botones se renderizan por estado y su tap muestra snackbar informativo "Disponible en la próxima versión" (strings ES/EN); nunca navegan ni llaman al BE.
- **Polling**: primer tick inmediato; siguientes en múltiplos de 30 s respecto a `timeProvider()` (inyectable). Ahorro de batería cancela el job de polling y lo relanza al restaurar.
- **Escanear ahora**: spinner mientras el POST /scan está en vuelo y refresco tras el 202 (también con `running:true`); con Reduce Motion (animator scale 0) el spinner se sustituye por "Escaneando…". `ScanNowButton` es `internal` (recibe `reduceMotion` como parámetro) para poder testear las dos ramas.
- **Corrección post-commit (revisión de la verificación)**: la fila descubierta (managed=false) mostraba "Encender" en lugar de "Dar de alta" cuando estaba offline — UX-DR5 dice "descubierta → solo Alta"; corregido el `when` (managed se evalúa antes que offline) + tests "fila descubierta offline muestra solo dar de alta" y "fila gestionada offline muestra estado off y accion encender".
- **Correcciones del triaje de revisión (todos con test o verificación)**: sprint-status sin clave duplicada; cleartext HTTP permitido (AD-6/Deferred TLS); `scanNow()` marca offline con fallo; batería low/power-save reactiva vía BroadcastReceiver; empty state nunca con fallo de red; refreshes serializados (job cancelable) con `isRefreshing` real; reloj elapsedRealtime + módulo seguro; `fromWire` desconocido → NO_FIABLE; TalkBack etiqueta IP/MAC; `.gitignore` del árbol android; guard de doble scan; `distinctBy` contra ids duplicados; `Locale.ROOT`; `ApiException` en body malformado; `require(10_000..300_000)`; security-crypto/DataStore declaradas.
- **Gap de cobertura documentado**: no hay test de pantalla completa (`MachineListScreen` con `viewModel()` real) — requeriría fijar BuildConfig/activity de test; los call-sites son únicos y verificados por compilación; se cubrirá en 1.6 con la pantalla de ajustes.
- **Verificación**: `:app:compileDebugKotlin`, `:app:testDebugUnitTest` (24 tests Robolectric verdes: 15 ViewModel + 9 fila/scan) y `:app:assembleDebug` (APK generado) pasan en local.
- **Pendiente (no de esta story)**: lógica de acciones (Epic 2), ajustes/Keystore/token (1.6) y primer arranque (1.6). En 1.6 revisar si `ComponentActivity` sigue siendo necesario.

## Spec Change Log

## Review Triage Log

- blind-hunter + edge-case + verification `sprint-status.yaml clave 1-5 duplicada (review+backlog)` — **high** — verificado con `yaml.safe_load`/`sprint_plan.py validate`: el par colapsa a backlog y el YAML es inválido. **patch**: eliminada la línea `backlog` residual; queda solo `review`; YAML validado.
- blind-hunter `cleartext HTTP bloqueado en API 28+` — **high** — verificado: URL BuildConfig en http y sin `usesCleartextTraffic`; el BE se sirve por HTTP dentro de la tailnet (AD-6, TLS diferido en spine §Deferred). **patch**: `android:usesCleartextTraffic="true"` + comentario.
- blind-hunter `scanNow() traga fallos` — **medium** — **patch**: `onFailure` marca `isOffline`; test `escanar ahora con escaneo fallido no rompe la lista y refresca igualmente`.
- blind-hunter + edge-case + verification `batería muestreada una sola vez / isBatteryLow dead code` — **medium** — **patch**: `produceState` + `BroadcastReceiver` (`ACTION_POWER_SAVE_MODE_CHANGED` + `ACTION_BATTERY_LOW`) que re-evalúa `isBatterySaverActive || isBatteryLow` en caliente.
- blind-hunter + edge-case `primer fetch sin caché → empty state engañoso` — **medium** — **patch**: `isEmpty=false` siempre que falle el fetch (el empty state solo con 200 [] real); test `fetch fallido sin cache muestra badge sin conexion (no empty state)`.
- blind-hunter + edge-case `refresh sin serializar (race poll/PullToRefresh/scan) + silent dead` — **medium** — **patch**: `refreshJob` con cancelación (último gana); `silent` ahora controla `isRefreshing` (pull-to-refresh atado a la operación real, sin parpadeo).
- blind-hunter + edge-case `polling con reloj de pared` — **medium** — **patch**: `timeProvider` default → `SystemClock.elapsedRealtime()` + módulo seguro (`(elapsed % interval + interval) % interval`) ante saltos NTP/mano.
- blind-hunter `status wire desconocido → OFFLINE (habilitaría acciones de energía)` — **high** — **patch**: `fromWire` else → `NO_FIABLE` (badge ámbar, sin acciones destructivas); test `fromWire mapea estados y desconocido degrada a no fiable`.
- blind-hunter `TalkBack anuncia IP/MAC como un blob (sin etiqueta)` — **low** — **patch**: `semantics contentDescription` con "IP … MAC …" usando los strings existentes.
- blind-hunter `machines_pull_to_refresh_text sin uso` — **low** — sin patch (string reservado para el indicador del sistema cuando se consume el componente de M3; no se elimina por ser parte de la superfície de strings ES/EN).
- blind-hunter `faltaba .gitignore del árbol android/` — **medium** — **patch**: creado `android/.gitignore` (`build/`, `.gradle/`, `local.properties`, `.kotlin/`, apks…); verificado que no se han commiteado artefactos.
- blind-hunter `reduce-motion cacheado en remember + segunda acción de scan en empty state` — **low** — **patch parcial**: `EmptyState` deshabilita el botón mientras `isScanning`; la heurística `remember(context)` se conserva (se re-evalúa al recomponerse la pantalla; limitación documentada en Implementation Notes).
- blind-hunter `202 hardcodeado para /scan + body malformado sin envelope` — **medium** — **patch**: el 202 es el único 2xx del contrato (AD-3 verbatim) → se mantiene; body malformado ahora se captura como `ApiException("bad_response")` uniforme; test `body malformado con 200 lanza ApiException y la lista queda cacheada`.
- edge-case `pollingIntervalMs fuera de 10-300 s` — **low** — **patch**: `require(pollingIntervalMs in 10_000..300_000)` en el init del VM (FR-12).
- edge-case `doble POST /scan (segundo tap en vuelo)` — **medium** — **patch**: guard `if (isScanning) return` en `scanNow()`; test `doble escanar ahora solo dispara un POST en vuelo`.
- edge-case `ids duplicados rompen LazyColumn (DuplicateKey)` — **medium** — **patch**: `distinctBy { it.id }` al guardar la lista en el VM.
- edge-case `lowercase() locale-dependiente (turco)` — **low** — **patch**: `Locale.ROOT` en `fromWire`.
- edge-case + verification `security-crypto/DataStore afirmadas pero no declaradas` — **medium** — **patch**: declaradas en `app/build.gradle.kts` (1.1.0 / 1.2.1).
- verification `Authorization nunca asertada (dropping del header pasaría con 17 tests verdes)` — **high** — verificado: MockEngine no inspecciona headers; **patch**: log del header en el MockEngine (Triple) + aserto `Bearer t` en `primer fetch` y en `escanar ahora envía el token en el POST scan`.
- verification `binding de pantalla sin test (ScanNowButton con onClick={})` — **medium** — **defer-nota**: test de pantalla completa con `viewModel()` real requeriría fijar BuildConfig/activity; documentado como gap de cobertura en Implementation Notes; el cableado es un único call-site verificado por compilación.
- verification `deserialización DTO sin test` — **medium** — **patch**: test `deserializacion del DTO del contrato` (JSON verbatim del 1.4).
