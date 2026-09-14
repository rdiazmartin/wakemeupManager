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
    ): MachineListViewModel =
        MachineListViewModel(
            api = api,
            cache = cache,
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
    fun `ahorro de bateria pausa el polling y al restaurar se reanuda`() = runTest {
        val (api, log) = okApi()
        val vm = newViewModel(api, pollMs = 30_000L)
        vm.start()
        runCurrent()
        assertThat(log.filter { it.first == "GET" }).hasSize(1)

        // Ahorro de batería: el tiempo puede pasar sin que se produzcan fetches.
        vm.setBatterySaver(true)
        advanceTimeBy(300_000L)
        runCurrent()
        assertThat(log.filter { it.first == "GET" }).hasSize(1)

        // Al restaurar, el polling se relanza y refresca (NFR-10).
        vm.setBatterySaver(false)
        runCurrent()
        runCurrent()
        assertThat(log.filter { it.first == "GET" }).hasSize(2)

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
}

