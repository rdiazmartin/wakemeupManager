package com.wakemeup.manager.data.remote

import com.wakemeup.manager.data.local.SettingsRepository
import io.ktor.client.HttpClient
import io.ktor.client.call.body
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.client.request.bearerAuth
import io.ktor.client.request.get
import io.ktor.client.request.post
import io.ktor.client.statement.HttpResponse
import io.ktor.http.HttpStatusCode
import io.ktor.http.isSuccess
import io.ktor.serialization.kotlinx.json.json
import kotlinx.serialization.json.Json

/** Extensión local: 2xx = éxito (HttpStatusCode no la expone como propiedad en Ktor 3.x). */
private fun HttpStatusCode.isSuccessCode(): Boolean = value in 200..299

/** Error de negocio uniforme del BE (envelope 1.4) o de transporte. */
class ApiException(
    val code: String,
    override val message: String,
) : Exception(message)

/**
 * Cliente REST del contrato 1.4 (`GET /api/v1/machines`, `POST /api/v1/scan`).
 * La URL y el token salen del [SettingsRepository] (1.5: BuildConfig; 1.6: Keystore+DataStore).
 */
class WakemeupApi(
    private val settings: SettingsRepository,
    private val client: HttpClient = defaultClient(),
) {
    suspend fun listMachines(): List<com.wakemeup.manager.domain.Machine> {
        val response: HttpResponse = client.get("${settings.apiUrl}/machines") {
            bearerAuth(settings.deviceToken)
        }
        if (!response.status.isSuccessCode()) throw response.toApiException("/machines")
        // Body malformado (200 sin JSON del contrato) → ApiException uniforme,
        // no una SerializationException cruda (envelope AD-1).
        return try {
            response.body<MachinesEnvelopeDto>().machines.map { it.toDomain() }
        } catch (_: Exception) {
            throw ApiException("bad_response", "respuesta inválida de /machines")
        }
    }

    suspend fun triggerScan(): com.wakemeup.manager.domain.ScanResult {
        val response: HttpResponse = client.post("${settings.apiUrl}/scan") {
            bearerAuth(settings.deviceToken)
        }
        if (response.status != HttpStatusCode.Accepted) throw response.toApiException("/scan")
        return try {
            response.body<ScanEnvelopeDto>().scan.toDomain()
        } catch (_: Exception) {
            throw ApiException("bad_response", "respuesta inválida de /scan")
        }
    }

    /**
     * Valida unas credenciales CONTRA `GET /machines` (decisión 1.6, OQ-1):
     * el healthcheck `/status` está exento de auth en el BE (AD-6) y nunca
     * devolvería 401; `/machines` es el único endpoint autenticado del
     * contrato 1.4 y distingue token rechazado (`ApiException` 401), BE
     * inalcanzable (excepción de transporte) y conexión OK (200).
     *
     * Recibe la URL y el token A VALIDAR (los del formulario), no la caché
     * del [com.wakemeup.manager.data.local.SettingsRepository]: en el primer
     * arranque la caché está vacía y una validación contra ella fallaría
     * siempre en un dispositivo real.
     */
    suspend fun validateConnection(apiUrl: String, deviceToken: String) {
        val response: HttpResponse = client.get("$apiUrl/machines") {
            bearerAuth(deviceToken)
        }
        if (!response.status.isSuccessCode()) throw response.toApiException("/machines")
    }

    private suspend fun HttpResponse.toApiException(endpointNote: String): ApiException {
        return try {
            val payload = body<ApiErrorEnvelopeDto>()
            ApiException(payload.error.code, payload.error.message)
        } catch (_: Exception) {
            ApiException("http_${status.value}", "HTTP ${status.value} al llamar a $endpointNote")
        }
    }

    companion object {
        fun defaultClient(): HttpClient = HttpClient {
            expectSuccess = false
            install(HttpTimeout) {
                requestTimeoutMillis = 15_000
                connectTimeoutMillis = 10_000
            }
            install(ContentNegotiation) {
                json(
                    Json {
                        ignoreUnknownKeys = true
                        explicitNulls = false
                    }
                )
            }
        }

        fun defaultJson(): Json = Json {
            ignoreUnknownKeys = true
            explicitNulls = false
        }
    }
}
