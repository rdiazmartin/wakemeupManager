package com.wakemeup.manager.domain

import java.util.Locale

/**
 * Origen de un evento de estado del bus del BE (AD-11, epic 3):
 * `origin ∈ {api, mcp, scan, periodic}`. Un valor desconocido (versión futura
 * del BE) no rompe el stream: degrada a [UNKNOWN].
 */
enum class EventOrigin(val wire: String) {
    API("api"),
    MCP("mcp"),
    SCAN("scan"),
    PERIODIC("periodic"),
    UNKNOWN("unknown");

    companion object {
        fun fromWire(value: String): EventOrigin =
            entries.firstOrNull { it.wire.equals(value, ignoreCase = true) } ?: UNKNOWN
    }
}

/**
 * Acción que representa un evento del stream, derivada de su `type` wire
 * (`wake_sent`/`shutdown_done`/`machine_online`/`machine_offline`/
 * `machine_no_fiable`/`enroll_done`/`scan_done`). El texto de la notificación
 * (título = máquina, cuerpo = acción) se resuelve en la capa de notificaciones
 * a partir de esta acción.
 */
enum class EventAction {
    WAKE,
    SHUTDOWN,
    ONLINE,
    OFFLINE,
    NO_FIABLE,
    ENROLL,
    SCAN,
    UNKNOWN;

    /**
     * true solo para acciones que afectan a una máquina concreta. `scan_done`
     * (máquina "-") y los tipos desconocidos no son acciones de máquina y no
     * deben notificar (FR-18).
     */
    val isMachineAction: Boolean get() = this != SCAN && this != UNKNOWN

    companion object {
        fun fromType(type: String): EventAction = when (type.lowercase(Locale.ROOT)) {
            "wake_sent" -> WAKE
            "shutdown_done" -> SHUTDOWN
            "machine_online" -> ONLINE
            "machine_offline" -> OFFLINE
            "machine_no_fiable" -> NO_FIABLE
            "enroll_done" -> ENROLL
            "scan_done" -> SCAN
            else -> UNKNOWN
        }
    }
}

/**
 * Evento de estado recibido por el stream SSE (`GET /api/v1/events`):
 * `{type, machine, timestamp, origin}`. `machine` es la etiqueta de la máquina
 * afectada (hostname o IP) y `timestamp` un ISO-8601 UTC.
 */
data class MachineEvent(
    val type: String,
    val machine: String,
    val timestamp: String,
    val origin: EventOrigin,
) {
    val action: EventAction get() = EventAction.fromType(type)

    /** Transición de estado que implica el evento, o null si no cambia estado. */
    val statusChange: MachineStatus?
        get() = when (action) {
            EventAction.ONLINE -> MachineStatus.ONLINE
            EventAction.OFFLINE -> MachineStatus.OFFLINE
            EventAction.NO_FIABLE -> MachineStatus.NO_FIABLE
            else -> null
        }
}
