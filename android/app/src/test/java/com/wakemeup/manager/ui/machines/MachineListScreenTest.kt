package com.wakemeup.manager.ui.machines

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextReplacement
import com.google.common.truth.Truth.assertThat
import com.wakemeup.manager.data.local.InMemorySettingsRepository
import com.wakemeup.manager.data.local.MachinesCache
import com.wakemeup.manager.data.remote.WakemeupApi
import com.wakemeup.manager.domain.Machine
import com.wakemeup.manager.domain.MachineStatus
import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.MockEngineConfig
import io.ktor.client.engine.mock.MockRequestHandler
import io.ktor.client.engine.mock.respond
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import io.ktor.serialization.kotlinx.json.json
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Cobertura Robolectric de la pantalla para el epic 2 (UX-DR3/DR6/DR7):
 * el diálogo de apagado SIEMPRE precede al POST /shutdown, el diálogo de alta
 * no persiste la password (queda solo en el estado local del diálogo), el error
 * del BE se muestra inline y las acciones fallidas permiten reintento sin
 * salir de la pantalla. Como MainActivityTest: viewModelScope sobre el looper
 * principal real de Robolectric (sin scheduler virtual).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "es")
class MachineListScreenTest {

    @get:Rule
    val compose = createComposeRule()

    private fun managedOnline() = Machine(
        id = 1,
        name = "Desktop",
        ip = "192.168.1.10",
        mac = "AA:BB:CC:DD:EE:01",
        hostname = "desktop",
        status = MachineStatus.ONLINE,
        managed = true,
    )

    private fun discovered() = Machine(
        id = 2,
        name = "Pi",
        ip = "192.168.1.11",
        mac = "AA:BB:CC:DD:EE:02",
        hostname = "pi",
        status = MachineStatus.OFFLINE,
        managed = false,
    )

    private fun machinesJson() =
        """{"machines":[{"id":1,"name":"Desktop","ip":"192.168.1.10","mac":"AA:BB:CC:DD:EE:01","hostname":"desktop","status":"online","managed":true},{"id":2,"name":"Pi","ip":"192.168.1.11","mac":"AA:BB:CC:DD:EE:02","hostname":"pi","status":"offline","managed":false}]}"""

    private fun jsonHeaders() = headersOf(HttpHeaders.ContentType, "application/json")

    /**
     * ViewModel con scope real (Dispatchers.Main de Robolectric): sin
     * scheduler virtual, como MainActivityTest. El MockEngine usa su
     * dispatcher por defecto; `waitForIdle` bombea el looper principal.
     */
    private fun screenViewModel(
        handler: MockRequestHandler = { request ->
            if (request.url.toString().endsWith("/machines")) {
                respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
            } else {
                respond("""{"ok":true}""", HttpStatusCode.OK, jsonHeaders())
            }
        },
    ): MachineListViewModel {
        val client = HttpClient(MockEngine(MockEngineConfig().apply { addHandler(handler) })) {
            expectSuccess = false
            install(ContentNegotiation) {
                json(WakemeupApi.defaultJson())
            }
        }
        return MachineListViewModel(
            api = WakemeupApi(
                settings = InMemorySettingsRepository("http://test/api/v1", "t"),
                client = client,
            ),
            cache = MachinesCache(),
            pollingIntervalMs = 30_000,
            timeProvider = { 0L },
        )
    }

    @Test
    fun `tap en apagar abre el dialogo de confirmacion siempre`() {
        val vm = screenViewModel()
        compose.setContent { MachineListScreen(viewModel = vm) }
        compose.waitForIdle()
        compose.onNodeWithText("Desktop").assertIsDisplayed()

        compose.onNodeWithText("Apagar").performClick()
        compose.waitForIdle()

        // UX-DR3: diálogo obligatorio y único; nunca tap silencioso.
        compose.onNodeWithText("¿Apagar la máquina?").assertIsDisplayed()
        assertThat(vm.dialog.value).isInstanceOf(MachineDialog.Shutdown::class.java)
    }

    @Test
    fun `confirmar el dialogo envia el shutdown`() {
        var sawShutdown = false
        val vm = screenViewModel(handler = { request ->
            if (request.url.toString().endsWith("/shutdown")) {
                sawShutdown = true
                respond("""{"ok":true}""", HttpStatusCode.OK, jsonHeaders())
            } else {
                respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
            }
        })
        compose.setContent { MachineListScreen(viewModel = vm) }
        compose.waitForIdle()

        compose.onNodeWithText("Apagar").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("shutdown_dialog_confirm").performClick()
        compose.waitForIdle()

        assertThat(sawShutdown).isTrue()
        assertThat(vm.dialog.value).isNull()
    }

    @Test
    fun `cancelar el dialogo no envia nada`() {
        var sawShutdown = false
        val vm = screenViewModel(handler = { request ->
            if (request.url.toString().endsWith("/shutdown")) {
                sawShutdown = true
                respond("""{"ok":true}""", HttpStatusCode.OK, jsonHeaders())
            } else {
                respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
            }
        })
        compose.setContent { MachineListScreen(viewModel = vm) }
        compose.waitForIdle()

        compose.onNodeWithText("Apagar").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("shutdown_dialog_cancel").performClick()
        compose.waitForIdle()

        assertThat(sawShutdown).isFalse()
        assertThat(vm.dialog.value).isNull()
    }

    @Test
    fun `alta abre el dialogo con aviso de password de un solo uso`() {
        val vm = screenViewModel()
        compose.setContent { MachineListScreen(viewModel = vm) }
        compose.waitForIdle()
        compose.onNodeWithText("Pi").assertIsDisplayed()

        compose.onNodeWithText("Dar de alta").performClick()
        compose.waitForIdle()

        compose.onNodeWithText("La password se usa una sola vez y no se guarda.").assertIsDisplayed()
        compose.onNodeWithTag("enroll_user_field").assertIsDisplayed()
        compose.onNodeWithTag("enroll_password_field").assertIsDisplayed()
    }

    @Test
    fun `alta envia el POST enroll con usuario y password y cierra`() {
        var captured: String? = null
        val vm = screenViewModel(handler = { request ->
            if (request.url.toString().endsWith("/enroll")) {
                captured = (request.body as io.ktor.http.content.TextContent).text
                respond("""{"id":2,"managed":true}""", HttpStatusCode.OK, jsonHeaders())
            } else {
                respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
            }
        })
        compose.setContent { MachineListScreen(viewModel = vm) }
        compose.waitForIdle()

        compose.onNodeWithText("Dar de alta").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("enroll_user_field").performTextInput("maria")
        compose.onNodeWithTag("enroll_password_field").performTextInput("s3cr3t")
        compose.onNodeWithTag("enroll_dialog_confirm").performClick()
        compose.waitForIdle()

        assertThat(captured).contains("\"usuario\":\"maria\"")
        assertThat(captured).contains("\"password\":\"s3cr3t\"")
        assertThat(vm.dialog.value).isNull()
    }

    @Test
    fun `la password del alta no se persiste en ningun almacen`() {
        // El diálogo solo mantiene la password en su estado local (no en el
        // ViewModel); el SettingsRepository de la app nunca recibe el alta.
        val vm = screenViewModel()
        compose.setContent { MachineListScreen(viewModel = vm) }
        compose.waitForIdle()

        compose.onNodeWithText("Dar de alta").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("enroll_user_field").performTextInput("maria")
        compose.onNodeWithTag("enroll_password_field").performTextInput("s3cr3t-temporal")
        compose.onNodeWithTag("enroll_dialog_confirm").performClick()
        compose.waitForIdle()

        // Tras el alta exitosa el diálogo se cierra y el ViewModel no conserva
        // ninguna propiedad con la password (solo stateFlow de UI/diálogo).
        assertThat(vm.dialog.value).isNull()
        assertThat(vm.pendingAction.value).isNull()
    }

    @Test
    fun `error inline del alta permite reintentar sin salir de la pantalla`() {
        var failing = true
        val vm = screenViewModel(handler = { request ->
            if (request.url.toString().endsWith("/enroll")) {
                if (failing) {
                    respond(
                        """{"error":{"code":"unauthorized","message":"credenciales incorrectas"}}""",
                        HttpStatusCode.Unauthorized,
                        jsonHeaders(),
                    )
                } else {
                    respond("""{"id":2,"managed":true}""", HttpStatusCode.OK, jsonHeaders())
                }
            } else {
                respond(machinesJson(), HttpStatusCode.OK, jsonHeaders())
            }
        })
        compose.setContent { MachineListScreen(viewModel = vm) }
        compose.waitForIdle()

        compose.onNodeWithText("Dar de alta").performClick()
        compose.waitForIdle()
        compose.onNodeWithTag("enroll_user_field").performTextInput("maria")
        compose.onNodeWithTag("enroll_password_field").performTextInput("mala")
        compose.onNodeWithTag("enroll_dialog_confirm").performClick()
        compose.waitForIdle()

        // Error inline (UX-DR6); el diálogo sigue abierto para reintentar.
        compose.onNodeWithTag("enroll_error").assertIsDisplayed()
        assertThat(vm.dialog.value).isInstanceOf(MachineDialog.Enroll::class.java)

        // Reintento con password correcta (sin salir de la pantalla, UX-DR7).
        failing = false
        compose.onNodeWithTag("enroll_password_field").performTextReplacement("s3cr3t")
        compose.onNodeWithTag("enroll_dialog_confirm").performClick()
        compose.waitForIdle()

        assertThat(vm.dialog.value).isNull()
        assertThat(vm.consumeActionMessage()).isNotNull()
    }
}
