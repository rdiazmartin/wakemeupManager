package com.wakemeup.manager.data.remote

import com.google.common.truth.Truth.assertThat
import com.wakemeup.manager.data.local.InMemorySettingsRepository
import com.wakemeup.manager.domain.EventOrigin
import io.ktor.client.HttpClient
import io.ktor.client.engine.HttpClientEngineBase
import io.ktor.client.engine.HttpClientEngineCapability
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.MockEngineConfig
import io.ktor.client.engine.mock.respond
import io.ktor.client.plugins.HttpTimeoutCapability
import io.ktor.client.plugins.HttpTimeoutConfig
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.plugins.sse.SSECapability
import io.ktor.client.request.HttpRequestData
import io.ktor.client.request.HttpResponseData
import io.ktor.client.request.ResponseAdapterAttributeKey
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import io.ktor.serialization.kotlinx.json.json
import io.ktor.utils.io.ByteReadChannel
import io.ktor.utils.io.InternalAPI
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.take
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import org.junit.Test

/**
 * Cobertura JVM del cliente SSE (epic 3, story 3.4): parsing robusto (evento
 * mal formado ignorado), reconexión con backoff silenciosa, 401 → sesión
 * inválida y URL/Authorization correctos (AD-11).
 *
 * El MockEngine de Ktor no declara `SSECapability` ni aplica el
 * `ResponseAdapter` de Ktor (eso lo hace el engine real, p. ej. OkHttp), así
 * que se envuelve en un engine mínimo que añade la capacidad y aplica el
 * adaptador de la respuesta, replicando el comportamiento del engine real.
 * El flujo SSE abre su propia corrutina interna en el scope del cliente, por lo
 * que no se usa Robolectric ni scheduler virtual: `runBlocking` con el engine
 * sobre `Dispatchers.IO`.
 */
@OptIn(InternalAPI::class)
class WakemeupEventStreamTest {

    /** MockEngine + `SSECapability` + adaptación SSE (como el engine real). */
    private class SseCapableMockEngine(
        private val delegate: MockEngine,
    ) : HttpClientEngineBase("sse-mock") {
        override val config get() = delegate.config
        override val supportedCapabilities: Set<HttpClientEngineCapability<*>> =
            delegate.supportedCapabilities + SSECapability

        override suspend fun execute(data: HttpRequestData): HttpResponseData {
            val response = delegate.execute(data)
            val body = response.body
            val adapted: Any = if (body is ByteReadChannel) {
                data.attributes.getOrNull(ResponseAdapterAttributeKey)
                    ?.adapt(data, response.statusCode, response.headers, body, data.body, response.callContext)
                    ?: body
            } else {
                body
            }
            return HttpResponseData(
                response.statusCode,
                response.requestTime,
                response.headers,
                response.version,
                adapted,
                response.callContext,
            )
        }

        override fun close() {
            delegate.close()
            super.close()
        }
    }

    private val sseHeaders = headersOf(HttpHeaders.ContentType, "text/event-stream")

    private fun eventJson(
        type: String,
        machine: String,
        origin: String,
        timestamp: String = "2026-09-15T10:00:00+00:00",
    ) = """{"type":"$type","machine":"$machine","timestamp":"$timestamp","origin":"$origin"}"""

    private fun client(handler: io.ktor.client.engine.mock.MockRequestHandler): HttpClient {
        val config = MockEngineConfig().apply {
            dispatcher = Dispatchers.IO
            addHandler(handler)
        }
        val mock = MockEngine(config)
        return HttpClient(SseCapableMockEngine(mock)) {
            expectSuccess = false
            install(io.ktor.client.plugins.sse.SSE)
            // Mismo requestTimeout que el cliente REST de producción (15 s): el
            // SSE debe sobreescribirlo a infinito por petición.
            install(io.ktor.client.plugins.HttpTimeout) {
                requestTimeoutMillis = 15_000
            }
            install(ContentNegotiation) { json(WakemeupApi.defaultJson()) }
        }
    }

    private fun settings() = InMemorySettingsRepository(
        apiUrl = "http://test/api/v1",
        deviceToken = "tok",
    )

    @Test
    fun `parsea eventos data y usa el endpoint events con Bearer`() = runBlocking {
        val body = buildString {
            append(": connected\n\n")
            append("data: ${eventJson("machine_online", "NAS", "mcp")}\n\n")
            append(": keep-alive\n\n")
            append("data: ${eventJson("machine_offline", "Pi", "periodic")}\n\n")
        }
        var seenUrl: String? = null
        var seenAuth: String? = null
        var seenRequest: HttpRequestData? = null
        val client = client { request ->
            seenUrl = request.url.toString()
            seenAuth = request.headers["Authorization"]
            seenRequest = request
            respond(body, HttpStatusCode.OK, sseHeaders)
        }
        val stream = WakemeupEventStream(settings(), client)

        val events = stream.events().take(2).toList()

        assertThat(events).hasSize(2)
        assertThat(events[0].machine).isEqualTo("NAS")
        assertThat(events[0].origin).isEqualTo(EventOrigin.MCP)
        assertThat(events[0].type).isEqualTo("machine_online")
        assertThat(events[1].machine).isEqualTo("Pi")
        assertThat(events[1].origin).isEqualTo(EventOrigin.PERIODIC)
        // URL y Authorization exactos (contrato /events, AD-6/AD-11).
        assertThat(seenUrl).isEqualTo("http://test/api/v1/events")
        assertThat(seenAuth).isEqualTo("Bearer tok")
        // El request SSE no hereda el timeout de 15 s: va a infinito.
        val timeout = seenRequest!!.getCapabilityOrNull(HttpTimeoutCapability)
        assertThat(timeout?.requestTimeoutMillis).isEqualTo(HttpTimeoutConfig.INFINITE_TIMEOUT_MS)
    }

    @Test
    fun `evento mal formado se ignora sin romper el stream`() = runBlocking {
        val body = buildString {
            append(": connected\n\n")
            append("data: esto-no-es-json\n\n")
            append("data: ${eventJson("machine_online", "NAS", "mcp")}\n\n")
        }
        val client = client { respond(body, HttpStatusCode.OK, sseHeaders) }
        val stream = WakemeupEventStream(settings(), client)

        // El único evento válido llega aunque antes hubiera basura.
        val event = stream.events().first()

        assertThat(event.machine).isEqualTo("NAS")
        assertThat(event.origin).isEqualTo(EventOrigin.MCP)
    }

    @Test
    fun `reconecta con backoff tras corte y vuelve a pedir el stream`() = runBlocking {
        var calls = 0
        val client = client { request ->
            calls++
            if (calls == 1) {
                respond("boom", HttpStatusCode.ServiceUnavailable)
            } else {
                respond(
                    "data: ${eventJson("machine_online", "NAS", "mcp")}\n\n",
                    HttpStatusCode.OK,
                    sseHeaders,
                )
            }
        }
        val stream = WakemeupEventStream(
            settings(),
            client,
            initialBackoffMs = 10L,
            maxBackoffMs = 50L,
        )

        // El primer intento falla; el flujo espera el backoff y reintenta.
        val event = stream.events().first()

        assertThat(event.machine).isEqualTo("NAS")
        assertThat(calls).isAtLeast(2)
    }

    @Test
    fun `401 en el stream lanza unauthorized sin reintentar`() = runBlocking {
        var calls = 0
        val client = client {
            calls++
            respond(
                """{"error":{"code":"unauthorized","message":"revocado"}}""",
                HttpStatusCode.Unauthorized,
                headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }
        val stream = WakemeupEventStream(settings(), client, initialBackoffMs = 10L)

        val error = runCatching { stream.events().first() }.exceptionOrNull()

        assertThat(error).isInstanceOf(ApiException::class.java)
        assertThat((error as ApiException).code).isEqualTo("unauthorized")
        assertThat(calls).isEqualTo(1)
    }
}
