package com.wakemeup.manager.data.remote

import com.wakemeup.manager.domain.Machine
import com.wakemeup.manager.domain.MachineStatus
import com.wakemeup.manager.domain.ScanResult
import kotlinx.serialization.Serializable

/**
 * DTOs del contrato fijado en la story 1.4 (`backend/src/wakemeup/api/__init__.py`), verbatim.
 *
 * `GET /api/v1/machines` -> {"machines": [{id,name,ip,mac,hostname,status,managed}]}
 * `POST /api/v1/scan` -> 202 {"scan": {running,triggered,discovered,duration_ms}}
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
) {
    fun toDomain(): Machine = Machine(
        id = id,
        name = name,
        ip = ip,
        mac = mac,
        hostname = hostname,
        status = MachineStatus.fromWire(status),
        managed = managed,
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

@Serializable
data class ApiErrorPayloadDto(
    val code: String,
    val message: String,
)

@Serializable
data class ApiErrorEnvelopeDto(
    val error: ApiErrorPayloadDto,
)
