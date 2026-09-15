package com.wakemeup.manager.notifications

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.wakemeup.manager.R
import com.wakemeup.manager.domain.EventAction
import com.wakemeup.manager.domain.EventOrigin
import com.wakemeup.manager.domain.MachineEvent
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.asSharedFlow

/**
 * Estado del permiso `POST_NOTIFICATIONS` (Android 13+) para la app.
 *
 * `alreadyRequested` recuerda que ya se pidió (para no pedirlo en bucle: si se
 * denegó, la app sugiere activarlo en ajustes). En Android < 13 no hay permiso
 * de runtime: se usa `areNotificationsEnabled()` como estado efectivo.
 */
interface NotificationPermission {
    fun isGranted(): Boolean
    fun alreadyRequested(): Boolean
    fun markRequested()

    /** true solo si existe permiso de runtime (Android 13+): se puede pedir. */
    fun supportsRuntimeRequest(): Boolean
}

/** Implementación real sobre el permiso del sistema + flag persistido. */
class AndroidNotificationPermission(private val context: Context) : NotificationPermission {

    override fun isGranted(): Boolean {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            return ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS,
            ) == PackageManager.PERMISSION_GRANTED
        }
        // < 13: sin permiso de runtime; se respeta el toggle de la app.
        return NotificationManagerCompat.from(context).areNotificationsEnabled()
    }

    override fun alreadyRequested(): Boolean =
        prefs().getBoolean(KEY_ASKED, false)

    override fun markRequested() {
        prefs().edit().putBoolean(KEY_ASKED, true).apply()
    }

    override fun supportsRuntimeRequest(): Boolean =
        Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU

    private fun prefs() = context.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    private companion object {
        const val FILE = "wakemeup_notifications"
        const val KEY_ASKED = "post_notifications_asked"
    }
}

/**
 * Notificador del sistema para acciones del agente (epic 3, FR-18/AD-11).
 *
 * Solo los eventos con origen `mcp` publican notificación (título = máquina,
 * cuerpo = acción, channel propio); el resto de orígenes únicamente actualizan
 * la fila, que se hace en el ViewModel. Sin permiso, la notificación se omite:
 * se emite un prompt para pedirlo (primera vez) o para sugerir ajustes; la fila
 * se refleja igual. La notificación se dispara aquí, en la capa que recibe el
 * evento, nunca desde la UI.
 */
class AndroidMachineNotifier(
    private val context: Context,
    private val permission: NotificationPermission = AndroidNotificationPermission(context),
) : MachineNotifier {

    /**
     * replay = 1: el primer prompt no se pierde aunque ningún colector esté
     * suscrito todavía (evita que `markRequested()` consuma la única petición).
     */
    private val prompts = MutableSharedFlow<NotificationPermissionPrompt>(
        replay = 1,
        extraBufferCapacity = 1,
    )
    override val permissionPrompts: SharedFlow<NotificationPermissionPrompt> = prompts.asSharedFlow()

    /** La sugerencia de ajustes se emite como mucho una vez por sesión. */
    private var settingsSuggestionEmitted = false

    init {
        createChannel()
    }

    override fun notify(event: MachineEvent) {
        // Solo el agente IA notifica; api/scan/periodic solo actualizan la fila.
        if (event.origin != EventOrigin.MCP) return
        if (!permission.isGranted()) {
            if (!permission.supportsRuntimeRequest() || permission.alreadyRequested()) {
                // Sin permiso de runtime (o ya pedido y denegado): sugerir
                // ajustes una sola vez, sin volver a pedirlo en bucle.
                if (!settingsSuggestionEmitted) {
                    settingsSuggestionEmitted = true
                    prompts.tryEmit(NotificationPermissionPrompt.SETTINGS)
                }
            } else {
                permission.markRequested()
                prompts.tryEmit(NotificationPermissionPrompt.REQUEST)
            }
            return
        }
        post(event)
    }

    private fun post(event: MachineEvent) {
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(event.machine)
            .setContentText(bodyFor(event.action))
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify(event.machine.hashCode(), notification)
    }

    private fun bodyFor(action: EventAction): String = when (action) {
        EventAction.WAKE -> context.getString(R.string.agent_notification_body_wake)
        EventAction.SHUTDOWN -> context.getString(R.string.agent_notification_body_shutdown)
        else -> context.getString(R.string.agent_notification_body_generic)
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        val channel = NotificationChannel(
            CHANNEL_ID,
            context.getString(R.string.agent_notification_channel_name),
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply {
            description = context.getString(R.string.agent_notification_channel_description)
        }
        manager.createNotificationChannel(channel)
    }

    companion object {
        const val CHANNEL_ID = "wakemeup_agent"
    }
}
