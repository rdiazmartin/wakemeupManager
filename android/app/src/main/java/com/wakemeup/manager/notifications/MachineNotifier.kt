package com.wakemeup.manager.notifications

import com.wakemeup.manager.domain.MachineEvent
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow

/**
 * Solicitud dirigida a la capa UI para resolver el permiso `POST_NOTIFICATIONS`
 * (la notificación en sí se dispara en la capa que recibe el evento, no aquí).
 */
enum class NotificationPermissionPrompt {
    /** Primer evento `mcp` sin permiso: pedirlo con explicación (Android 13+). */
    REQUEST,

    /** Ya se pidió y se denegó: sugerir activarlo en ajustes, sin volver a pedirlo. */
    SETTINGS,
}

/**
 * Emisor de notificaciones del sistema para acciones del agente (epic 3, FR-18).
 *
 * Solo los eventos de origen `mcp` notifican; el resto de orígenes únicamente
 * actualizan la fila (sin snackbar ni spinner, UX-DR7). La [notify] se invoca
 * desde la capa que recibe el evento (ViewModel), nunca desde la UI.
 */
interface MachineNotifier {
    /**
     * Procesa un evento recibido: si su origen es `mcp`, publica la
     * notificación (con permiso concedido) o emite un prompt en
     * [permissionPrompts] si falta permiso; en cualquier caso la fila ya se ha
     * actualizado aparte.
     */
    fun notify(event: MachineEvent)

    /**
     * Prompts de permiso pendientes de resolver por la UI. El colector debe
     * estar suscrito antes del primer evento `mcp` (el permiso se resuelve de
     * forma oportunista, sin bloquear el stream).
     */
    val permissionPrompts: SharedFlow<NotificationPermissionPrompt>

    companion object {
        /** Doble por defecto: no notifica ni pide permisos (tests/no-op). */
        fun noop(): MachineNotifier = object : MachineNotifier {
            override fun notify(event: MachineEvent) = Unit

            override val permissionPrompts =
                MutableSharedFlow<NotificationPermissionPrompt>(extraBufferCapacity = 1)
        }
    }
}
