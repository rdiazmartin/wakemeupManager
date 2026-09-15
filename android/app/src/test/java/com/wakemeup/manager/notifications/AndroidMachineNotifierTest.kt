package com.wakemeup.manager.notifications

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import androidx.test.core.app.ApplicationProvider
import com.google.common.truth.Truth.assertThat
import com.wakemeup.manager.domain.EventOrigin
import com.wakemeup.manager.domain.MachineEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.yield
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * Cobertura Robolectric del notificador del sistema (epic 3, story 3.5):
 * solo los eventos de origen `mcp` publican notificación en el channel propio;
 * `api`/`scan`/`periodic` no notifican (solo actualizan la fila, en el VM);
 * sin permiso `POST_NOTIFICATIONS` se omite la notificación y se emite el prompt
 * para pedirlo (primera vez) o sugerir ajustes (ya pedido).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "es")
class AndroidMachineNotifierTest {

    private val context: Context = ApplicationProvider.getApplicationContext()

    private fun event(origin: EventOrigin, type: String = "shutdown_done") = MachineEvent(
        type = type,
        machine = "NAS",
        timestamp = "2026-09-15T10:00:00+00:00",
        origin = origin,
    )

    private fun grantNotifications() {
        shadowOf(context as android.app.Application)
            .grantPermissions(Manifest.permission.POST_NOTIFICATIONS)
    }

    private fun notifications() =
        shadowOf(context.getSystemService(NotificationManager::class.java)).allNotifications

    @Test
    fun `evento mcp con permiso publica notificacion titulo maquina y cuerpo accion`() {
        grantNotifications()
        val notifier = AndroidMachineNotifier(context)

        notifier.notify(event(EventOrigin.MCP))

        assertThat(notifications()).hasSize(1)
        val posted = notifications().first()
        assertThat(posted.channelId).isEqualTo(AndroidMachineNotifier.CHANNEL_ID)
        val shadow = shadowOf(posted)
        assertThat(shadow.contentTitle).isEqualTo("NAS")
        assertThat(shadow.contentText.toString()).contains("apagó")
    }

    @Test
    fun `origenes api scan periodic no publican notificacion`() {
        grantNotifications()
        val notifier = AndroidMachineNotifier(context)

        notifier.notify(event(EventOrigin.API))
        notifier.notify(event(EventOrigin.SCAN))
        notifier.notify(event(EventOrigin.PERIODIC))

        assertThat(notifications()).isEmpty()
    }

    @Test
    fun `permiso denegado no publica y emite prompt de peticion`() {
        // Sin grantPermissions: POST_NOTIFICATIONS denegado.
        val notifier = AndroidMachineNotifier(context)
        val prompts = mutableListOf<NotificationPermissionPrompt>()
        val scope = CoroutineScope(Dispatchers.Unconfined + SupervisorJob())
        scope.launch { notifier.permissionPrompts.collect { prompts.add(it) } }
        runBlocking { yield() }

        notifier.notify(event(EventOrigin.MCP))
        runBlocking { yield() }

        assertThat(notifications()).isEmpty()
        assertThat(prompts).contains(NotificationPermissionPrompt.REQUEST)
        scope.cancel()
    }

    @Test
    fun `permiso denegado ya pedido emite sugerencia de ajustes`() {
        val permission = FakePermission(granted = false, alreadyRequested = true)
        val notifier = AndroidMachineNotifier(context, permission)
        val prompts = mutableListOf<NotificationPermissionPrompt>()
        val scope = CoroutineScope(Dispatchers.Unconfined + SupervisorJob())
        scope.launch { notifier.permissionPrompts.collect { prompts.add(it) } }
        runBlocking { yield() }

        notifier.notify(event(EventOrigin.MCP))
        runBlocking { yield() }

        assertThat(notifications()).isEmpty()
        assertThat(prompts).contains(NotificationPermissionPrompt.SETTINGS)
        scope.cancel()
    }

    @Test
    fun `el prompt de peticion sobrevive sin colector y llega al suscribirse`() {
        // Notifica ANTES de que exista ningún colector: con replay=1 el prompt
        // no se pierde aunque `markRequested()` ya haya corrido.
        val notifier = AndroidMachineNotifier(context)
        notifier.notify(event(EventOrigin.MCP))

        val prompts = mutableListOf<NotificationPermissionPrompt>()
        val scope = CoroutineScope(Dispatchers.Unconfined + SupervisorJob())
        scope.launch { notifier.permissionPrompts.collect { prompts.add(it) } }
        runBlocking { yield() }

        assertThat(prompts).containsExactly(NotificationPermissionPrompt.REQUEST)
        scope.cancel()
    }

    @Test
    fun `la sugerencia de ajustes se emite una sola vez`() {
        val permission = FakePermission(granted = false, alreadyRequested = true)
        val notifier = AndroidMachineNotifier(context, permission)
        val prompts = mutableListOf<NotificationPermissionPrompt>()
        val scope = CoroutineScope(Dispatchers.Unconfined + SupervisorJob())
        scope.launch { notifier.permissionPrompts.collect { prompts.add(it) } }
        runBlocking { yield() }

        notifier.notify(event(EventOrigin.MCP))
        notifier.notify(event(EventOrigin.MCP))
        notifier.notify(event(EventOrigin.MCP))
        runBlocking { yield() }

        // Aunque lleguen varios eventos mcp, la sugerencia se emite at most once.
        assertThat(prompts.count { it == NotificationPermissionPrompt.SETTINGS }).isEqualTo(1)
        scope.cancel()
    }

    private class FakePermission(
        private val granted: Boolean,
        private val alreadyRequested: Boolean,
    ) : NotificationPermission {
        override fun isGranted(): Boolean = granted
        override fun alreadyRequested(): Boolean = alreadyRequested
        override fun markRequested() = Unit
        override fun supportsRuntimeRequest(): Boolean = true
    }
}
