package com.wakemeup.manager.ui.machines

import com.google.common.truth.Truth.assertThat
import com.wakemeup.manager.data.local.InMemorySettingsRepository
import com.wakemeup.manager.data.local.MachinesCache
import com.wakemeup.manager.data.remote.WakemeupApi
import com.wakemeup.manager.domain.Machine
import com.wakemeup.manager.domain.MachineStatus
import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.MockEngineConfig
import io.ktor.client.engine.mock.respond
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Cobertura Robolectric del ViewModel (requisito transversal de tests):
 * poll a 30 s (10–300 s), escanear ahora (spinner + refresco, también con
 * escaneo en curso), caché off-line con badge "Sin conexión", empty state
 * y pausa del polling con ahorro de batería (NFR-10).
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class MachineListViewModelTest {

    // --- helpers ---------------------------------------------------------

    private val sampleMachines: List<Machine> = listOf(
        Machine(
            id = 1,
            name = "Desktop",
            ip = "192.168.1.10",
            mac = "AA:BB:CC:DD:EE:01",
            hostname = "desktop",
            status = MachineStatus.ONLINE,
            managed = false,
        ),
        Machine(
            id = 2,
            name = "Pi",
            ip = "192.168.1.11",
            mac = null,
            hostname = "pi",
            status = MachineStatus.OFFLINE,
            managed = false,
        ),
    )

    private fun machinesJson(machines: List<Machine> = sampleMachines): String {
        val parts = machines.joinToString(",") { m ->
            buildString {
                append("{\"id\":${m.id},\"name\":\"${m.name}\",\"ip\":\"${m.ip}\"")
                m.mac?.let { append(",\"mac\":\"$it\"") }
                m.hostname?.let { append(",\"hostname\":\"$it\"") }
                append(",\"status\":\"${m.status.name.lowercase()}\",\"managed\":${m.managed}}")
            }
        }
        return """{"machines":[$parts]}"""
    }

    private fun scanJson(running: Boolean = false) =
        """{"scan":{"running":$running,"triggered":${!running},"discovered":2,"duration_ms":123}}"""

    private fun jsonHeaders() = headersOf(HttpHeaders.ContentType, "application/json")

    /**
     * Cliente Ktor sobre MockEngine con JSON del contrato (sin red real).
     * El dispatcher del engine se ata al scheduler virtual del test para que
     * toda la cadena (request → handler → response → state) sea determinista
     * y no dependa de hilos reales.
     */
    private fun TestScope.client(handler: io.ktor.client.engine.mock.MockRequestHandler): HttpClient {
        val config = MockEngineConfig().apply {
            dispatcher = StandardTestDispatcher(testScheduler)
            addHandler(handler)
        }
        return HttpClient(MockEngine(config)) {
            expectSuccess = false
            install(ContentNegotiation) {
                json(WakemeupApi.defaultJson())
            }
        }
    }

    /** API OK: GET /machines devuelve 2 máquinas; POST /scan devuelve 202. */
    private fun TestScope.okApi(scanRunning: Boolean = false): Pair<WakemeupApi, MutableList<Triple<String, String, String>>> {
        // Triple: (method, url, Authorization) para poder afirmar el header.
        val log = mutableListOf<Triple<String, String, String>>()
        val api = WakemeupApi(
            settings = InMemorySettingsRepository(apiUrl = "http://test/api/v1", deviceToken = "t"),
            client = client { request ->
                log.add(
                    Triple(
                        request.method.value,
                        request.url.toString(),
                        request.headers["Authorization"] ?: "",
                    )
                )
                when {
                    request.url.toString().endsWith("/machines") ->
                        respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                    else ->
                        respond(scanJson(running = scanRunning), HttpStatusCode.Accepted, jsonHeaders())
                }
            },
        )
        return api to log
    }

    /**
     * Crea el ViewModel con el scheduler virtual del test. Nota de entorno:
     * con kotlinx-coroutines-test 1.11 el scheduling virtual requiere
     * [runCurrent] para tareas inmediatas y [advanceTimeBy] para temporizadores;
     * jamás [kotlinx.coroutines.test.advanceUntilIdle] con un polling activo
     * (el bucle no llega nunca a idle y desborda el heap).
     */
    private fun TestScope.newViewModel(
        api: WakemeupApi,
        cache: MachinesCache = MachinesCache(),
        pollMs: Long = MachineListViewModel.DEFAULT_POLL_INTERVAL_MS,
        eventStream: com.wakemeup.manager.data.remote.EventStream = com.wakemeup.manager.data.remote.EventStream.noop(),
        notifier: com.wakemeup.manager.notifications.MachineNotifier = com.wakemeup.manager.notifications.MachineNotifier.noop(),
    ): MachineListViewModel =
        MachineListViewModel(
            api = api,
            cache = cache,
            eventStream = eventStream,
            notifier = notifier,
            pollingIntervalMs = pollMs,
            timeProvider = { 0L },
            externalScope = backgroundScope,
        )

    // --- tests -----------------------------------------------------------

    @Test
    fun `primer fetch carga la lista y limpia loading y empty`() = runTest {
        val (api, log) = okApi()
        val vm = newViewModel(api)

        assertThat(vm.uiState.value.isLoading).isTrue()

        vm.start()
        runCurrent()

        val state = vm.uiState.value
        assertThat(state.isLoading).isFalse()
        assertThat(state.machines).hasSize(2)
        assertThat(state.machines[0].name).isEqualTo("Desktop")
        assertThat(state.isEmpty).isFalse()
        assertThat(state.isOffline).isFalse()
        // El token viaja como Bearer en cada petición (contrato 1.4, AD-6).
        assertThat(log[0].third).isEqualTo("Bearer t")
    }

    @Test
    fun `escanar ahora envía el token en el POST scan`() = runTest {
        val (api, log) = okApi()
        val vm = newViewModel(api)
        vm.start()
        runCurrent()

        vm.scanNow()
        runCurrent()

        val scan = log.single { it.first == "POST" }
        assertThat(scan.second).endsWith("/scan")
        assertThat(scan.third).isEqualTo("Bearer t")
    }

    @Test
    fun `fetch fallido con cache muestra lista cacheada con badge sin conexion`() = runTest {
        val cache = MachinesCache()
        cache.save(sampleMachines)

        val failing = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { respond("boom", HttpStatusCode.ServiceUnavailable) },
        )
        val vm = newViewModel(failing, cache = cache)
        vm.start()
        runCurrent()

        val state = vm.uiState.value
        assertThat(state.isLoading).isFalse()
        assertThat(state.isOffline).isTrue()
        assertThat(state.machines).hasSize(2)
        assertThat(state.machines[0].name).isEqualTo("Desktop")
    }

    @Test
    fun `lista vacia muestra empty state`() = runTest {
        val empty = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { respond("""{"machines":[]}""", HttpStatusCode.OK, jsonHeaders()) },
        )
        val vm = newViewModel(empty)
        vm.start()
        runCurrent()

        val state = vm.uiState.value
        assertThat(state.isLoading).isFalse()
        assertThat(state.isEmpty).isTrue()
        assertThat(state.machines).isEmpty()
        assertThat(state.isOffline).isFalse()
    }

    @Test
    fun `escanar ahora hace POST scan con spinner y refresca la lista`() = runTest {
        // Handler de /scan con retardo virtual para observar el estado intermedio.
        val log = mutableListOf<Pair<String, String>>()
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { request ->
                log.add(request.method.value to request.url.toString())
                when {
                    request.url.toString().endsWith("/machines") ->
                        respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                    else -> {
                        delay(5_000)
                        respond(scanJson(), HttpStatusCode.Accepted, jsonHeaders())
                    }
                }
            },
        )
        val vm = newViewModel(api)
        vm.start()
        runCurrent()
        assertThat(log.filter { it.first == "GET" }).hasSize(1)

        vm.scanNow()
        runCurrent() // arranca el coroutine: spinner activo y POST en vuelo
        assertThat(vm.uiState.value.isScanning).isTrue()

        advanceTimeBy(5_000L) // el POST /scan completa
        runCurrent()

        assertThat(vm.uiState.value.isScanning).isFalse()
        assertThat(log.filter { it.first == "POST" }.any { it.second.endsWith("/scan") }).isTrue()
        // Refresco posterior al 202 (AD-3): la lista vuelve a consultarse.
        assertThat(log.filter { it.first == "GET" }).hasSize(2)
        assertThat(vm.uiState.value.machines).hasSize(2)
        assertThat(vm.uiState.value.isOffline).isFalse()
    }

    @Test
    fun `escanar ahora con escaneo en curso (running true) no rompe la lista`() = runTest {
        val (api, _) = okApi(scanRunning = true)
        val vm = newViewModel(api)
        vm.start()
        runCurrent()

        vm.scanNow()
        runCurrent()

        assertThat(vm.uiState.value.isScanning).isFalse()
        assertThat(vm.uiState.value.machines).hasSize(2)
        assertThat(vm.uiState.value.isOffline).isFalse()
    }

    @Test
    fun `escanar ahora con escaneo fallido no rompe la lista y refresca igualmente`() = runTest {
        // El POST /scan puede fallar (red, BE caído): nunca un crash; el
        // spinner se limpia y la lista se intenta refrescar igualmente
        // (el refresh posterior con 200 limpia el badge y conserva las máquinas).
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { request ->
                when {
                    request.url.toString().endsWith("/machines") ->
                        respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                    else ->
                        respond(
                            """{"error":{"code":"internal_error","message":"boom"}}""",
                            HttpStatusCode.InternalServerError,
                            jsonHeaders(),
                        )
                }
            },
        )
        val vm = newViewModel(api)
        vm.start()
        runCurrent()

        vm.scanNow()
        runCurrent()

        assertThat(vm.uiState.value.isScanning).isFalse()
        assertThat(vm.uiState.value.isOffline).isFalse() // refresh exitoso tras el fallo
        assertThat(vm.uiState.value.machines).hasSize(2)
    }

    @Test
    fun `polling refresca silenciosamente cada intervalo`() = runTest {
        val (api, log) = okApi()
        val vm = newViewModel(api, pollMs = 30_000L)
        vm.start()
        runCurrent()
        assertThat(log.filter { it.first == "GET" }).hasSize(1)

        advanceTimeBy(30_000L)
        runCurrent()
        assertThat(log.filter { it.first == "GET" }).hasSize(2)

        advanceTimeBy(30_000L)
        runCurrent()
        assertThat(log.filter { it.first == "GET" }).hasSize(3)
        assertThat(vm.uiState.value.isOffline).isFalse()
    }

    @Test
    fun `doble escanar ahora solo dispara un POST en vuelo`() = runTest {
        // Handler con /scan lento; el segundo scanNow debe ignorarse mientras
        // el primero está en vuelo (guard de isScanning).
        val postLog = mutableListOf<Long>()
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { request ->
                when {
                    request.url.toString().endsWith("/machines") ->
                        respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                    else -> {
                        postLog.add(System.nanoTime())
                        delay(5_000)
                        respond(scanJson(), HttpStatusCode.Accepted, jsonHeaders())
                    }
                }
            },
        )
        val vm = newViewModel(api)
        vm.start()
        runCurrent()

        vm.scanNow()
        runCurrent()
        assertThat(vm.uiState.value.isScanning).isTrue()
        vm.scanNow() // segundo tap mientras el escaneo vuela
        advanceTimeBy(5_000L)
        runCurrent()
        runCurrent()

        assertThat(postLog).hasSize(1)
        assertThat(vm.uiState.value.isScanning).isFalse()
    }

    @Test
    fun `fetch fallido sin cache muestra badge sin conexion (no empty state)`() = runTest {
        // UX-DR4: un fallo de red en el primer fetch NO es "no hay máquinas";
        // el badge "Sin conexión" se muestra sin contenido cacheado.
        val failing = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { respond("boom", HttpStatusCode.ServiceUnavailable) },
        )
        val vm = newViewModel(failing) // caché vacía
        vm.start()
        runCurrent()

        val state = vm.uiState.value
        assertThat(state.isLoading).isFalse()
        assertThat(state.isOffline).isTrue()
        assertThat(state.isEmpty).isFalse() // no empty state engañoso
        assertThat(state.machines).isEmpty()
    }

    @Test
    fun `body malformado con 200 lanza ApiException y la lista queda cacheada`() = runTest {
        val cache = MachinesCache()
        cache.save(sampleMachines)
        val weird = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { respond("not-json", HttpStatusCode.OK, jsonHeaders()) },
        )
        val vm = newViewModel(weird, cache = cache)
        vm.start()
        runCurrent()

        // El error de deserialización se captura como fallo de red genérico.
        assertThat(vm.uiState.value.isOffline).isTrue()
        assertThat(vm.uiState.value.machines).hasSize(2)
    }

    @Test
    fun `fromWire mapea estados y desconocido degrada a no fiable`() = runTest {
        // Trigo 1.5: un status no previsto del BE (versión futura) → NO_FIABLE
        // (badge ámbar, sin acciones destructivas), nunca OFFLINE (que habilitaría
        // acciones de energía sobre un estado mal entendido).
        assertThat(MachineStatus.fromWire("online")).isEqualTo(MachineStatus.ONLINE)
        assertThat(MachineStatus.fromWire("offline")).isEqualTo(MachineStatus.OFFLINE)
        assertThat(MachineStatus.fromWire("no_fiable")).isEqualTo(MachineStatus.NO_FIABLE)
        assertThat(MachineStatus.fromWire("suspenden")).isEqualTo(MachineStatus.NO_FIABLE)
    }

    @Test
    fun `deserializacion del DTO del contrato`() = runTest {
        // El JSON es el del contrato 1.4 verbatim (managed presente, status libre).
        val raw = """{"machines":[{"id":1,"name":"Desktop","ip":"192.168.1.10","mac":"AA:BB:CC:DD:EE:01","hostname":"desktop","status":"online","managed":false}]}"""
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { respond(raw, HttpStatusCode.OK, jsonHeaders()) },
        )
        val vm = newViewModel(api)
        vm.start()
        runCurrent()
        val m = vm.uiState.value.machines.single()
        assertThat(m.name).isEqualTo("Desktop")
        assertThat(m.status).isEqualTo(MachineStatus.ONLINE)
        assertThat(m.managed).isFalse()
    }

    @Test
    fun `401 en fetch emite sesion invalida sin marcar offline`() = runTest {
        val unauthorized = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client {
                respond(
                    """{"error":{"code":"unauthorized","message":"revocado"}}""",
                    HttpStatusCode.Unauthorized,
                    jsonHeaders(),
                )
            },
        )
        val vm = newViewModel(unauthorized)
        val seen = mutableListOf<Unit>()
        // No hay collect en el test: usamos un collect en backgroundScope.
        backgroundScope.launch { vm.sessionInvalid.collect { seen.add(it) } }
        vm.start()
        runCurrent()

        assertThat(seen).hasSize(1)
        // Un 401 NO es un fallo de red: el badge de offline no se activa.
        assertThat(vm.uiState.value.isOffline).isFalse()
    }

    @Test
    fun `401 en scan emite sesion invalida`() = runTest {
        val unauthorized = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { request ->
                when {
                    request.url.toString().endsWith("/machines") ->
                        respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                    else ->
                        respond(
                            """{"error":{"code":"unauthorized","message":"revocado"}}""",
                            HttpStatusCode.Unauthorized,
                            jsonHeaders(),
                        )
                }
            },
        )
        val vm = newViewModel(unauthorized)
        val seen = mutableListOf<Unit>()
        backgroundScope.launch { vm.sessionInvalid.collect { seen.add(it) } }
        vm.start()
        runCurrent()

        vm.scanNow()
        runCurrent()

        assertThat(seen).hasSize(1)
        // El 401 del scan tampoco es un fallo de red: sin badge de offline,
        // y el refresco posterior (que también recibe 401) no lo marca.
        assertThat(vm.uiState.value.isOffline).isFalse()
        assertThat(vm.uiState.value.isScanning).isFalse()
    }

    @Test
    fun `ahorro de bateria pausa el SSE y mantiene el polling`() = runTest {
        val (api, log) = okApi()
        val stream = RecordingEventStream()
        val notifier = RecordingNotifier()
        val vm = newViewModel(api, pollMs = 30_000L, eventStream = stream, notifier = notifier)
        vm.start()
        runCurrent()
        assertThat(log.filter { it.first == "GET" }).hasSize(1)
        assertThat(stream.subscriptions).isEqualTo(1)

        // Ahorro de batería: el SSE se pausa, pero el polling sigue activo.
        vm.setBatterySaver(true)
        runCurrent()
        assertThat(stream.subscriptions).isEqualTo(1)
        assertThat(stream.active).isFalse()

        advanceTimeBy(30_000L)
        runCurrent()
        assertThat(log.filter { it.first == "GET" }).hasSize(2)

        // Al restaurar, el SSE se reabre (FR-12/FR-18).
        vm.setBatterySaver(false)
        runCurrent()
        assertThat(stream.subscriptions).isEqualTo(2)
        assertThat(stream.active).isTrue()

        advanceTimeBy(30_000L)
        runCurrent()
        assertThat(log.filter { it.first == "GET" }).hasSize(3)
    }

    @Test
    fun `fallo en un poll deja la lista cacheada y un poll posterior exitoso la limpia`() =
        runTest {
            var failing = true
            val cache = MachinesCache()
            cache.save(sampleMachines)

            val flaky = WakemeupApi(
                settings = InMemorySettingsRepository("http://test/api/v1", "t"),
                client = client {
                    if (failing) respond("boom", HttpStatusCode.ServiceUnavailable)
                    else respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                },
            )
            val vm = newViewModel(flaky, cache = cache, pollMs = 30_000L)
            vm.start()
            runCurrent()

            assertThat(vm.uiState.value.isOffline).isTrue()
            assertThat(vm.uiState.value.machines).hasSize(2)

            failing = false
            advanceTimeBy(30_000L)
            runCurrent()

            assertThat(vm.uiState.value.isOffline).isFalse()
            assertThat(vm.uiState.value.machines).hasSize(2)
        }

    // --- Epic 2: acciones de fila (alta/wake/shutdown) ---

    private fun managedMachine(id: Int = 1) = Machine(
        id = id,
        name = "Gestionada",
        ip = "192.168.1.20",
        mac = "AA:BB:CC:DD:EE:02",
        hostname = "gestionada",
        status = MachineStatus.OFFLINE,
        managed = true,
    )

    private val actionJson = """{"ok":true}"""

    private fun TestScope.controlApi(): Pair<WakemeupApi, MutableList<Pair<String, String>>> {
        val log = mutableListOf<Pair<String, String>>()  // (method, url)
        val api = WakemeupApi(
            settings = InMemorySettingsRepository(apiUrl = "http://test/api/v1", deviceToken = "t"),
            client = client { request ->
                log.add(request.method.value to request.url.toString())
                when {
                    request.url.toString().endsWith("/machines") ->
                        respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                    else ->
                        respond(actionJson, HttpStatusCode.OK, jsonHeaders())
                }
            },
        )
        return api to log
    }

    @Test
    fun `wake envia POST y emite snackbar de exito`() = runTest {
        val (api, log) = controlApi()
        val vm = newViewModel(api)
        vm.start()
        runCurrent()

        vm.wake(managedMachine())
        runCurrent()

        assertThat(log.any { it.first == "POST" && it.second.endsWith("/wake") }).isTrue()
        val msg = vm.consumeActionMessage()
        assertThat(msg).isNotNull()
        assertThat(msg!!.isError).isFalse()
        assertThat(vm.pendingAction.value).isNull()
    }

    @Test
    fun `wake fallido emite mensaje de error y permite reintento`() = runTest {
        var failing = true
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { request ->
                when {
                    request.url.toString().endsWith("/machines") ->
                        respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                    else -> {
                        if (failing) {
                            respond(
                                """{"error":{"code":"conflict","message":"máquina no gestionada"}}""",
                                HttpStatusCode.Conflict,
                                jsonHeaders(),
                            )
                        } else {
                            respond(actionJson, HttpStatusCode.OK, jsonHeaders())
                        }
                    }
                }
            },
        )
        val vm = newViewModel(api)
        vm.start()
        runCurrent()

        vm.wake(managedMachine())
        runCurrent()
        val msg = vm.consumeActionMessage()
        assertThat(msg).isNotNull()
        assertThat(msg!!.isError).isTrue()
        assertThat(vm.pendingAction.value).isNull()

        // Reintento sin salir de la pantalla (UX-DR7).
        failing = false
        vm.wake(managedMachine())
        runCurrent()
        val ok = vm.consumeActionMessage()
        assertThat(ok).isNotNull()
        assertThat(ok!!.isError).isFalse()
    }

    @Test
    fun `shutdown envia POST y emite snackbar de exito`() = runTest {
        val (api, log) = controlApi()
        val vm = newViewModel(api)
        vm.start()
        runCurrent()

        vm.shutdown(managedMachine(id = 1).copy(status = MachineStatus.ONLINE))
        runCurrent()

        assertThat(log.any { it.first == "POST" && it.second.endsWith("/shutdown") }).isTrue()
        assertThat(vm.pendingAction.value).isNull()
        val msg = vm.consumeActionMessage()
        assertThat(msg).isNotNull()
        assertThat(msg!!.isError).isFalse()
    }

    @Test
    fun `enroll envia usuario y password en el POST`() = runTest {
        val captured = mutableListOf<String>()
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { request ->
                when {
                    request.url.toString().endsWith("/machines") ->
                        respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                else -> {
                    captured.add(
                        (request.body as io.ktor.http.content.TextContent).text
                    )
                    respond(actionJson, HttpStatusCode.OK, jsonHeaders())
                }
                }
            },
        )
        val vm = newViewModel(api)
        vm.start()
        runCurrent()

        vm.enroll(
            Machine(
                id = 9, name = "Nueva", ip = "192.168.1.30", mac = null,
                hostname = "nueva", status = MachineStatus.OFFLINE, managed = false,
            ),
            "maria",
            "s3cr3t",
        )
        runCurrent()

        assertThat(captured).hasSize(1)
        assertThat(captured[0]).contains("\"usuario\":\"maria\"")
        assertThat(captured[0]).contains("\"password\":\"s3cr3t\"")
        // Tras el alta se refresca la lista (fruto del BE, FR-2).
        val msg = vm.consumeActionMessage()
        assertThat(msg).isNotNull()
        assertThat(msg!!.isError).isFalse()
    }

    @Test
    fun `enroll fallido muestra error inline y el dialogo sigue abierto`() = runTest {
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { request ->
                when {
                    request.url.toString().endsWith("/machines") ->
                        respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                    else ->
                        respond(
                            """{"error":{"code":"unauthorized","message":"credenciales incorrectas"}}""",
                            HttpStatusCode.Unauthorized,
                            jsonHeaders(),
                        )
                }
            },
        )
        val vm = newViewModel(api)
        vm.start()
        runCurrent()
        val nueva = Machine(
            id = 9, name = "Nueva", ip = "192.168.1.30", mac = null,
            hostname = "nueva", status = MachineStatus.OFFLINE, managed = false,
        )
        vm.openEnrollDialog(nueva)
        vm.enroll(nueva, "maria", "mala")
        runCurrent()

        assertThat(vm.enrollError.value).contains("credenciales incorrectas")
        assertThat(vm.pendingAction.value).isNull()
    }

    @Test
    fun `401 en wake emite sesion invalida sin snackbar de error`() = runTest {
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { request ->
                when {
                    request.url.toString().endsWith("/machines") ->
                        respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                    else ->
                        respond(
                            """{"error":{"code":"unauthorized","message":"revocado"}}""",
                            HttpStatusCode.Unauthorized,
                            jsonHeaders(),
                        )
                }
            },
        )
        val vm = newViewModel(api)
        val seen = mutableListOf<Unit>()
        backgroundScope.launch { vm.sessionInvalid.collect { seen.add(it) } }
        vm.start()
        runCurrent()

        vm.wake(managedMachine())
        runCurrent()

        assertThat(seen).hasSize(1)
        assertThat(vm.uiState.value.actionMessage).isNull()  // sin snackbar de error
    }

    @Test
    fun `no se lanzan acciones paralelas con pendingAction`() = runTest {
        val postLog = mutableListOf<String>()
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { request ->
                when {
                    request.url.toString().endsWith("/machines") ->
                        respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                    else -> {
                        postLog.add(request.url.toString())
                        kotlinx.coroutines.delay(5_000)
                        respond(actionJson, HttpStatusCode.OK, jsonHeaders())
                    }
                }
            },
        )
        val vm = newViewModel(api)
        vm.start()
        runCurrent()

        vm.wake(managedMachine())
        runCurrent()
        assertThat(vm.pendingAction.value).isNotNull()
        vm.wake(managedMachine())  // segundo tap ignorado mientras vuela
        advanceTimeBy(5_000L)
        runCurrent()

        // Solo el primer wake llega al wire (un POST de acción); el refresco
        // posterior es GET /machines, que no entra en postLog.
        assertThat(postLog).hasSize(1)
        assertThat(postLog[0]).endsWith("/wake")
        assertThat(vm.pendingAction.value).isNull()
    }

    @Test
    fun `dialogo de apagado y de alta se gestionan desde el viewmodel`() = runTest {
        val vm = newViewModel(okApi().first)
        val shutdownTarget = managedMachine(id = 1).copy(status = MachineStatus.ONLINE)
        vm.openShutdownDialog(shutdownTarget)
        assertThat(vm.dialog.value).isInstanceOf(MachineDialog.Shutdown::class.java)

        vm.dismissDialog()
        assertThat(vm.dialog.value).isNull()

        val target = Machine(
            id = 9, name = "Nueva", ip = "192.168.1.30", mac = null,
            hostname = "nueva", status = MachineStatus.OFFLINE, managed = false,
        )
        vm.openEnrollDialog(target)
        assertThat(vm.dialog.value).isInstanceOf(MachineDialog.Enroll::class.java)
        vm.dismissDialog()
        assertThat(vm.dialog.value).isNull()
    }

    @Test
    fun `shutdown con 409 conflict cierra el dialogo y emite snackbar de error`() = runTest {
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { request ->
                when {
                    request.url.toString().endsWith("/machines") ->
                        respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
                    else ->
                        respond(
                            """{"error":{"code":"conflict","message":"el host no coincide con el fingerprint fijado; máquina marcada no_fiable"}}""",
                            HttpStatusCode.Conflict,
                            jsonHeaders(),
                        )
                }
            },
        )
        val vm = newViewModel(api)
        vm.start()
        runCurrent()
        val gestionada = managedMachine(id = 1).copy(status = MachineStatus.ONLINE)
        vm.openShutdownDialog(gestionada)

        vm.shutdown(gestionada)
        runCurrent()

        // UX-DR7: el 409 (fingerprint_mismatch → no_fiable) cierra el diálogo y
        // va al snackbar; el reintento es a nivel de fila, no de diálogo.
        assertThat(vm.dialog.value).isNull()
        val msg = vm.consumeActionMessage()
        assertThat(msg).isNotNull()
        assertThat(msg!!.isError).isTrue()
        assertThat(msg.text).contains("fingerprint")
    }

    @Test
    fun `tras una accion exitosa se refresca la lista con GET machines`() = runTest {
        var enrolledNow = false
        val getLog = mutableListOf<String>()
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { request ->
                when {
                    request.url.toString().endsWith("/machines") -> {
                        getLog.add(request.url.toString())
                        // Tras el alta, el BE devuelve la máquina gestionada
                        // (FR-2: la confirmación del cambio llega del estado).
                        val json = if (enrolledNow) {
                            """{"machines":[{"id":1,"name":"Gestionada","ip":"192.168.1.20","mac":"AA:BB:CC:DD:EE:02","hostname":"gestionada","status":"offline","managed":true}]}"""
                        } else {
                            machinesJson()
                        }
                        respond(json, HttpStatusCode.OK, jsonHeaders())
                    }
                    else -> {
                        enrolledNow = true
                        respond(actionJson, HttpStatusCode.OK, jsonHeaders())
                    }
                }
            },
        )
        val vm = newViewModel(api)
        vm.start()
        runCurrent()
        assertThat(getLog).hasSize(1)

        val nueva = Machine(
            id = 9, name = "Nueva", ip = "192.168.1.30", mac = null,
            hostname = "nueva", status = MachineStatus.OFFLINE, managed = false,
        )
        vm.enroll(nueva, "maria", "s3cr3t")
        runCurrent()

        // FR-2: el control provoca un GET /machines de refresco…
        assertThat(getLog).hasSize(2)
        // …y la lista refleja el estado nuevo del BE (managed=true).
        assertThat(vm.uiState.value.machines.single().managed).isTrue()
    }

    // --- Epic 3: eventos SSE, fallback de polling y notificación (3.4/3.5) ---

    /** Fuente de eventos de test: expone un SharedFlow alimentable y cuenta suscripciones. */
    private class RecordingEventStream : com.wakemeup.manager.data.remote.EventStream {
        val events = kotlinx.coroutines.flow.MutableSharedFlow<com.wakemeup.manager.domain.MachineEvent>(
            extraBufferCapacity = 16,
        )
        var subscriptions = 0
            private set
        var active = false
            private set

        override fun events(): kotlinx.coroutines.flow.Flow<com.wakemeup.manager.domain.MachineEvent> =
            kotlinx.coroutines.flow.flow {
                subscriptions++
                active = true
                try {
                    events.collect { emit(it) }
                } finally {
                    active = false
                }
            }
    }

    /** Stream que falla al colectar: para el camino de 401 (y de error genérico). */
    private class ThrowingEventStream(private val error: Throwable) :
        com.wakemeup.manager.data.remote.EventStream {
        override fun events(): kotlinx.coroutines.flow.Flow<com.wakemeup.manager.domain.MachineEvent> =
            kotlinx.coroutines.flow.flow { throw error }
    }

    /** Notificador de test: registra lo notificado (y no toca Android). */
    private class RecordingNotifier : com.wakemeup.manager.notifications.MachineNotifier {
        val notified = mutableListOf<com.wakemeup.manager.domain.MachineEvent>()
        private val prompts =
            kotlinx.coroutines.flow.MutableSharedFlow<com.wakemeup.manager.notifications.NotificationPermissionPrompt>(
                extraBufferCapacity = 1,
            )
        override val permissionPrompts:
            kotlinx.coroutines.flow.SharedFlow<com.wakemeup.manager.notifications.NotificationPermissionPrompt> =
            prompts

        override fun notify(event: com.wakemeup.manager.domain.MachineEvent) {
            notified.add(event)
        }
    }

    private fun event(
        type: String,
        machine: String,
        origin: com.wakemeup.manager.domain.EventOrigin,
        timestamp: String = "2026-09-15T10:00:00+00:00",
    ) = com.wakemeup.manager.domain.MachineEvent(
        type = type,
        machine = machine,
        timestamp = timestamp,
        origin = origin,
    )

    @Test
    fun `evento mcp actualiza la fila sin spinner ni snackbar y notifica`() = runTest {
        val (api, _) = okApi()
        val stream = RecordingEventStream()
        val notifier = RecordingNotifier()
        val vm = newViewModel(api, eventStream = stream, notifier = notifier)
        vm.start()
        runCurrent()
        assertThat(vm.uiState.value.machines).hasSize(2)

        stream.events.tryEmit(event("machine_offline", "Desktop", com.wakemeup.manager.domain.EventOrigin.MCP))
        runCurrent()

        val updated = vm.uiState.value.machines.first { it.name == "Desktop" }
        assertThat(updated.status).isEqualTo(MachineStatus.OFFLINE)
        // Sin spinner ni snackbar (acciones ajenas, UX-DR7).
        assertThat(vm.uiState.value.isRefreshing).isFalse()
        assertThat(vm.uiState.value.actionMessage).isNull()
        // Origen mcp → notificación (FR-18).
        assertThat(notifier.notified).hasSize(1)
        assertThat(notifier.notified[0].machine).isEqualTo("Desktop")
    }

    @Test
    fun `evento api scan periodic no notifica pero actualiza la fila`() = runTest {
        val (api, _) = okApi()
        val stream = RecordingEventStream()
        val notifier = RecordingNotifier()
        val vm = newViewModel(api, eventStream = stream, notifier = notifier)
        vm.start()
        runCurrent()

        listOf(
            com.wakemeup.manager.domain.EventOrigin.API,
            com.wakemeup.manager.domain.EventOrigin.SCAN,
            com.wakemeup.manager.domain.EventOrigin.PERIODIC,
        ).forEach { origin ->
            stream.events.tryEmit(event("machine_online", "Desktop", origin))
            runCurrent()
        }

        assertThat(notifier.notified).isEmpty()
        assertThat(vm.uiState.value.machines.first { it.name == "Desktop" }.status)
            .isEqualTo(MachineStatus.ONLINE)
    }

    @Test
    fun `evento mal formado no rompe el viewmodel`() = runTest {
        val (api, _) = okApi()
        val stream = RecordingEventStream()
        val notifier = RecordingNotifier()
        val vm = newViewModel(api, eventStream = stream, notifier = notifier)
        vm.start()
        runCurrent()

        // Un tipo/origen desconocido degrada sin crash y sin notificación.
        stream.events.tryEmit(event("algo_raro", "Desktop", com.wakemeup.manager.domain.EventOrigin.UNKNOWN))
        runCurrent()

        assertThat(notifier.notified).isEmpty()
        // La fila sigue intacta (no hay transición que aplicar).
        assertThat(vm.uiState.value.machines.first { it.name == "Desktop" }.status)
            .isEqualTo(MachineStatus.ONLINE)
    }

    @Test
    fun `fallback de polling notifica un mcp nuevo por last_origin`() = runTest {
        var machinesJsonAnswer = machinesJson()
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { respond(machinesJsonAnswer, HttpStatusCode.OK, jsonHeaders()) },
        )
        val notifier = RecordingNotifier()
        val vm = newViewModel(api, notifier = notifier, pollMs = 30_000L)
        vm.start()
        runCurrent()
        // Primer poll: sin cambios mcp → no notifica.
        assertThat(notifier.notified).isEmpty()

        // El BE refleja un cambio mcp nuevo (otro timestamp).
        machinesJsonAnswer = """{"machines":[{"id":1,"name":"Desktop","ip":"192.168.1.10","mac":"AA:BB:CC:DD:EE:01","hostname":"desktop","status":"offline","managed":false,"last_origin":"mcp","last_change_at":"2026-09-15T10:05:00+00:00"}]}"""
        advanceTimeBy(30_000L)
        runCurrent()

        assertThat(notifier.notified).hasSize(1)
        assertThat(notifier.notified[0].machine).isEqualTo("Desktop")
        assertThat(notifier.notified[0].origin).isEqualTo(com.wakemeup.manager.domain.EventOrigin.MCP)
    }

    @Test
    fun `no duplica notificacion si el cambio ya llego por SSE`() = runTest {
        var machinesJsonAnswer = machinesJson()
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { respond(machinesJsonAnswer, HttpStatusCode.OK, jsonHeaders()) },
        )
        val stream = RecordingEventStream()
        val notifier = RecordingNotifier()
        val vm = newViewModel(api, eventStream = stream, notifier = notifier, pollMs = 30_000L)
        vm.start()
        runCurrent()

        // El mismo cambio llega primero por SSE (origen mcp).
        stream.events.tryEmit(
            event(
                "machine_offline",
                "Desktop",
                com.wakemeup.manager.domain.EventOrigin.MCP,
                timestamp = "2026-09-15T10:05:00+00:00",
            )
        )
        runCurrent()
        assertThat(notifier.notified).hasSize(1)

        // El polling posterior ve el mismo cambio (last_change_at ~ el del SSE).
        machinesJsonAnswer = """{"machines":[{"id":1,"name":"Desktop","ip":"192.168.1.10","mac":"AA:BB:CC:DD:EE:01","hostname":"desktop","status":"offline","managed":false,"last_origin":"mcp","last_change_at":"2026-09-15T10:05:01+00:00"}]}"""
        advanceTimeBy(30_000L)
        runCurrent()

        // No se duplica (misma marca dentro de la tolerancia).
        assertThat(notifier.notified).hasSize(1)
    }

    @Test
    fun `401 en el stream emite sesion invalida`() = runTest {
        val (api, _) = okApi()
        val vm = newViewModel(
            api,
            eventStream = ThrowingEventStream(
                com.wakemeup.manager.data.remote.ApiException("unauthorized", "revocado"),
            ),
        )
        val seen = mutableListOf<Unit>()
        backgroundScope.launch { vm.sessionInvalid.collect { seen.add(it) } }
        vm.start()
        runCurrent()

        assertThat(seen).hasSize(1)
    }

    @Test
    fun `error no-401 en el stream no emite sesion invalida`() = runTest {
        val (api, _) = okApi()
        val vm = newViewModel(
            api,
            eventStream = ThrowingEventStream(RuntimeException("corte de red")),
        )
        val seen = mutableListOf<Unit>()
        backgroundScope.launch { vm.sessionInvalid.collect { seen.add(it) } }
        vm.start()
        runCurrent()

        assertThat(seen).isEmpty()
    }

    @Test
    fun `polling primero y luego SSE del mismo cambio no notifica dos veces`() = runTest {
        var machinesJsonAnswer = machinesJson()
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { respond(machinesJsonAnswer, HttpStatusCode.OK, jsonHeaders()) },
        )
        val stream = RecordingEventStream()
        val notifier = RecordingNotifier()
        val vm = newViewModel(api, eventStream = stream, notifier = notifier, pollMs = 30_000L)
        vm.start()
        runCurrent()

        // El cambio mcp llega primero por polling.
        machinesJsonAnswer = """{"machines":[{"id":1,"name":"Desktop","ip":"192.168.1.10","mac":"AA:BB:CC:DD:EE:01","hostname":"desktop","status":"offline","managed":false,"last_origin":"mcp","last_change_at":"2026-09-15T10:05:00+00:00"}]}"""
        advanceTimeBy(30_000L)
        runCurrent()
        assertThat(notifier.notified).hasSize(1)

        // El mismo cambio llega después por SSE (misma marca): no se duplica.
        stream.events.tryEmit(
            event(
                "machine_offline",
                "Desktop",
                com.wakemeup.manager.domain.EventOrigin.MCP,
                timestamp = "2026-09-15T10:05:00+00:00",
            )
        )
        runCurrent()

        assertThat(notifier.notified).hasSize(1)
    }

    @Test
    fun `mcp scan_done con maquina guion no notifica`() = runTest {
        val (api, _) = okApi()
        val stream = RecordingEventStream()
        val notifier = RecordingNotifier()
        val vm = newViewModel(api, eventStream = stream, notifier = notifier)
        vm.start()
        runCurrent()

        stream.events.tryEmit(
            event("scan_done", "-", com.wakemeup.manager.domain.EventOrigin.MCP)
        )
        runCurrent()

        assertThat(notifier.notified).isEmpty()
    }

    @Test
    fun `primer poll con mcp historico no notifica y uno mas nuevo si`() = runTest {
        var machinesJsonAnswer =
            """{"machines":[{"id":1,"name":"Desktop","ip":"192.168.1.10","mac":"AA:BB:CC:DD:EE:01","hostname":"desktop","status":"offline","managed":false,"last_origin":"mcp","last_change_at":"2026-09-10T08:00:00+00:00"}]}"""
        val api = WakemeupApi(
            settings = InMemorySettingsRepository("http://test/api/v1", "t"),
            client = client { respond(machinesJsonAnswer, HttpStatusCode.OK, jsonHeaders()) },
        )
        val notifier = RecordingNotifier()
        val vm = newViewModel(api, notifier = notifier, pollMs = 30_000L)
        vm.start()
        runCurrent()

        // Arranque en frío: el cambio mcp persistido es histórico → no notifica.
        assertThat(notifier.notified).isEmpty()

        // Un cambio realmente nuevo (más tarde) sí notifica.
        machinesJsonAnswer =
            """{"machines":[{"id":1,"name":"Desktop","ip":"192.168.1.10","mac":"AA:BB:CC:DD:EE:01","hostname":"desktop","status":"offline","managed":false,"last_origin":"mcp","last_change_at":"2026-09-15T10:05:00+00:00"}]}"""
        advanceTimeBy(30_000L)
        runCurrent()

        assertThat(notifier.notified).hasSize(1)
        assertThat(notifier.notified[0].machine).isEqualTo("Desktop")
    }
}

