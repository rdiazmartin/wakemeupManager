package com.wakemeup.manager.data.remote

import com.wakemeup.manager.data.local.SettingsRepository
import com.wakemeup.manager.domain.MachineEvent
import io.ktor.client.HttpClient
import io.ktor.client.plugins.HttpTimeoutConfig
import io.ktor.client.plugins.sse.SSEClientException
import io.ktor.client.plugins.sse.sse
import io.ktor.client.plugins.timeout
import io.ktor.client.request.bearerAuth
import io.ktor.http.HttpStatusCode
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.flow.emptyFlow
import kotlinx.coroutines.isActive
import kotlinx.serialization.json.Json

/**
 * Fuente de eventos de estado del BE (epic 3, AD-11).
 *
 * La app consume el stream SSE de `GET /api/v1/events` (Bearer de token de
 * dispositivo) y expone los eventos como `Flow<MachineEvent>`. La interfaz
 * desacopla el ViewModel del transporte para poder inyectar un doble en tests.
 */
interface EventStream {
    /**
     * Emite los eventos recibidos del stream, reconectando con backoff
     * exponencial silenciosa ante caídas. Un evento mal formado se ignora sin
     * romper el stream. Un 401 del BE termina el flujo con [ApiException]
     * `unauthorized` (sesión inválida, no se reintenta).
     */
    fun events(): Flow<MachineEvent>

    companion object {
        /** Doble por defecto: sin eventos (tests/consumidores que no los usan). */
        fun noop(): EventStream = object : EventStream {
            override fun events(): Flow<MachineEvent> = emptyFlow()
        }
    }
}

/**
 * Implementación SSE con Ktor (el plugin viene en `ktor-client-core`; el engine
 * OkHttp declara `SSECapability`). Reintenta con backoff exponencial acotado y
 * descarta eventos mal formados (DTO tolerante, `ignoreUnknownKeys`).
 */
class WakemeupEventStream(
    private val settings: SettingsRepository,
    private val client: HttpClient = WakemeupApi.defaultClient(),
    private val initialBackoffMs: Long = DEFAULT_INITIAL_BACKOFF_MS,
    private val maxBackoffMs: Long = DEFAULT_MAX_BACKOFF_MS,
    private val json: Json = WakemeupApi.defaultJson(),
) : EventStream {

    override fun events(): Flow<MachineEvent> = channelFlow {
        var attempt = 0
        while (currentCoroutineContext().isActive) {
            try {
                client.sse(
                    urlString = "${settings.apiUrl}/events",
                    request = {
                        bearerAuth(settings.deviceToken)
                        // El request es de larga duración: sin esto hereda el
                        // requestTimeout de 15 s del cliente REST y el stream se
                        // cortaría en bucle. Solo afecta a esta petición.
                        timeout {
                            requestTimeoutMillis = HttpTimeoutConfig.INFINITE_TIMEOUT_MS
                        }
                    },
                ) {
                    // Handshake correcto: se reinicia el backoff.
                    attempt = 0
                    incoming.collect { sse ->
                        val payload = sse.data ?: return@collect
                        // Evento mal formado: se ignora sin tumbar el stream.
                        val event = runCatching {
                            json.decodeFromString<MachineEventDto>(payload)
                        }.getOrNull() ?: return@collect
                        send(event.toDomain())
                    }
                }
                // El servidor cerró el stream: se reconecta con el backoff.
            } catch (e: CancellationException) {
                throw e
            } catch (e: SSEClientException) {
                // 401: sesión inválida (token revocado) → no reintentar.
                if (e.response?.status == HttpStatusCode.Unauthorized) {
                    throw ApiException("unauthorized", "token de dispositivo inválido o revocado")
                }
                // Cualquier otro fallo de handshake se reintenta en silencio.
            } catch (_: Throwable) {
                // Corte de red: se reintenta con backoff sin avisar al usuario.
            }
            attempt++
            delay(backoffFor(attempt))
        }
    }.catch { e ->
        // Un 401 debe llegar al consumidor como sesión inválida; el resto de
        // fallos ya se reintentan dentro y nunca llegan aquí.
        if (e is ApiException && e.code == "unauthorized") throw e
    }

    private fun backoffFor(attempt: Int): Long {
        val shift = (attempt - 1).coerceIn(0, 16)
        val backoff = initialBackoffMs shl shift
        return if (backoff <= 0L || backoff > maxBackoffMs) maxBackoffMs else backoff
    }

    companion object {
        const val DEFAULT_INITIAL_BACKOFF_MS = 1_000L
        const val DEFAULT_MAX_BACKOFF_MS = 30_000L
    }
}
