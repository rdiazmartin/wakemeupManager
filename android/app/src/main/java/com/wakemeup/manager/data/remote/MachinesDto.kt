package com.wakemeup.manager.data.remote

import com.wakemeup.manager.domain.EventOrigin
import com.wakemeup.manager.domain.Machine
import com.wakemeup.manager.domain.MachineEvent
import com.wakemeup.manager.domain.MachineStatus
import com.wakemeup.manager.domain.ScanResult
import kotlinx.serialization.Serializable

/**
 * DTOs del contrato fijado en la story 1.4 (`backend/src/wakemeup/api/__init__.py`), verbatim.
 *
 * `GET /api/v1/machines` -> {"machines": [{id,name,ip,mac,hostname,status,managed,last_origin,last_change_at}]}
 * `POST /api/v1/scan` -> 202 {"scan": {running,triggered,discovered,duration_ms}}
 * `GET /api/v1/events` -> SSE `data: {type,machine,timestamp,origin}` (epic 3, AD-11)
 * Envelope de error: {"error": {code, message}}
 */

@Serializable
data class MachineDto(
    val id: Int,
    val name: String,
    val ip: String,
    val mac: String? = null,
    val hostname: String? = null,
    val status: String,
    val managed: Boolean = false,
    // Aditivos (epic 3, AD-10 intacto): fallback de notificación por polling.
    val last_origin: String? = null,
    val last_change_at: String? = null,
) {
    fun toDomain(): Machine = Machine(
        id = id,
        name = name,
        ip = ip,
        mac = mac,
        hostname = hostname,
        status = MachineStatus.fromWire(status),
        managed = managed,
        lastOrigin = last_origin?.let { EventOrigin.fromWire(it) },
        lastChangeAt = last_change_at,
    )
}

@Serializable
data class MachinesEnvelopeDto(
    val machines: List<MachineDto>,
)

@Serializable
data class ScanDto(
    val running: Boolean,
    val triggered: Boolean,
    val discovered: Int? = null,
    val duration_ms: Int? = null,
) {
    fun toDomain(): ScanResult = ScanResult(
        running = running,
        triggered = triggered,
        discovered = discovered,
        durationMs = duration_ms,
    )
}

@Serializable
data class ScanEnvelopeDto(
    val scan: ScanDto,
)

/**
 * Cuerpo del alta (contrato 2.1): `POST /machines/{id}/enroll` con
 * `{usuario, password}`. La password se transmite una sola vez y nunca se
 * persiste en la app (UX-DR6) ni en el BE (FR-6).
 */
@Serializable
data class EnrollRequestDto(
    val usuario: String,
    val password: String,
)

@Serializable
data class ApiErrorPayloadDto(
    val code: String,
    val message: String,
)

@Serializable
data class ApiErrorEnvelopeDto(
    val error: ApiErrorPayloadDto,
)

/**
 * Evento del stream SSE (`GET /api/v1/events`, epic 3/AD-11): la línea
 * `data: {...}` con `{type, machine, timestamp, origin}`. Campos desconocidos
 * se ignoran (`ignoreUnknownKeys`) y un evento mal formado se descarta en el
 * cliente sin romper el stream (DTO tolerante).
 */
@Serializable
data class MachineEventDto(
    val type: String,
    val machine: String,
    val timestamp: String,
    val origin: String,
) {
    fun toDomain(): MachineEvent = MachineEvent(
        type = type,
        machine = machine,
        timestamp = timestamp,
        origin = EventOrigin.fromWire(origin),
    )
}
