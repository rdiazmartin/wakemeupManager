package com.wakemeup.manager.domain

import java.util.Locale

/** Estado de una máquina según el contrato del BE (AD-10). */
enum class MachineStatus {
    ONLINE,
    OFFLINE,
    NO_FIABLE;

    companion object {
        fun fromWire(value: String): MachineStatus = when (value.lowercase(Locale.ROOT)) {
            "online" -> ONLINE
            "offline" -> OFFLINE
            "no_fiable" -> NO_FIABLE
            else -> NO_FIABLE
        }
    }
}

/** Máquina del inventario expuesto por `GET /api/v1/machines` (contrato 1.4). */
data class Machine(
    val id: Int,
    val name: String,
    val ip: String,
    val mac: String?,
    val hostname: String?,
    val status: MachineStatus,
    val managed: Boolean,
)

/**
 * Resultado de forzar un escaneo (`POST /api/v1/scan`, 202).
 * `running == true` indica que ya había un escaneo en marcha (el BE nunca responde 409).
 */
data class ScanResult(
    val running: Boolean,
    val triggered: Boolean,
    val discovered: Int?,
    val durationMs: Int?,
)
