package com.wakemeup.manager

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import com.google.common.truth.Truth.assertThat
import com.wakemeup.manager.data.local.SecretKeys
import com.wakemeup.manager.data.local.SecretStore
import com.wakemeup.manager.data.local.SecureSettingsRepository
import com.wakemeup.manager.domain.MachineEvent
import com.wakemeup.manager.notifications.AndroidMachineNotifier
import com.wakemeup.manager.notifications.MachineNotifier
import com.wakemeup.manager.notifications.NotificationPermissionPrompt
import com.wakemeup.manager.ui.theme.WakemeupTheme
import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.MockEngineConfig
import io.ktor.client.engine.mock.respond
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.SharedFlow
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.shadows.ShadowToast
import org.robolectric.annotation.Config

/**
 * Cobertura Robolectric del enrutado de pantallas (story 1.6): sin
 * configuración → primer arranque; configuración guardada → listado directo
 * (reutilización de sesión, FR-15); 401 revocado → limpieza y vuelta a
 * ajustes (sesión inválida). Cubre además el gap de pantalla completa
 * documentado en 1.5 (composición real con ViewModels reales).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "es")
class MainActivityTest {

    @get:Rule
    val composeRule = createComposeRule()

    /** Doble del notificador: la UI prueba emite los prompts a demanda. */
    private class FakeNotifier : MachineNotifier {
        private val prompts =
            MutableSharedFlow<NotificationPermissionPrompt>(replay = 1, extraBufferCapacity = 1)
        override val permissionPrompts: SharedFlow<NotificationPermissionPrompt> = prompts

        fun emit(prompt: NotificationPermissionPrompt) {
            prompts.tryEmit(prompt)
        }

        override fun notify(event: MachineEvent) = Unit
    }

    private val fakeNotifier = FakeNotifier()

    @Before
    fun installNotifier() {
        MainActivity.notifierFactory = { fakeNotifier }
    }

    @After
    fun restoreNotifier() {
        MainActivity.notifierFactory = { ctx -> AndroidMachineNotifier(ctx.applicationContext) }
    }

    private class InMemorySecretStore : SecretStore {
        private val map = mutableMapOf<String, String>()
        override fun getString(key: String): String? = map[key]
        override fun putString(key: String, value: String) {
            map[key] = value
        }
        override fun remove(key: String) {
            map.remove(key)
        }
    }

    /** Cliente con GET /machines respondiendo lista OK (sesión válida). */
    private fun okMachinesApi(): HttpClient = HttpClient(
        MockEngine(
            MockEngineConfig().apply {
                addHandler { request ->
                    if (request.url.toString().endsWith("/machines")) {
                        respond(
                            """{"machines":[{"id":1,"name":"Desktop","ip":"192.168.1.10","status":"online","managed":false}]}""",
                            HttpStatusCode.OK,
                            headersOf(HttpHeaders.ContentType, "application/json"),
                        )
                    } else {
                        respond(
                            """{"error":{"code":"unauthorized","message":"revocado"}}""",
                            HttpStatusCode.Unauthorized,
                            headersOf(HttpHeaders.ContentType, "application/json"),
                        )
                    }
                }
            }
        )
    ) {
        expectSuccess = false
        install(ContentNegotiation) {
            json(com.wakemeup.manager.data.remote.WakemeupApi.defaultJson())
        }
    }

    /** Cliente con GET /machines respondiendo 401 (token revocado). */
    private fun revokedApi(): HttpClient = HttpClient(
        MockEngine(
            MockEngineConfig().apply {
                addHandler {
                    respond(
                        """{"error":{"code":"unauthorized","message":"revocado"}}""",
                        HttpStatusCode.Unauthorized,
                        headersOf(HttpHeaders.ContentType, "application/json"),
                    )
                }
            }
        )
    ) {
        expectSuccess = false
        install(ContentNegotiation) {
            json(com.wakemeup.manager.data.remote.WakemeupApi.defaultJson())
        }
    }

    private fun configure(store: SecretStore): SecureSettingsRepository {
        store.putString(SecretKeys.API_URL, "http://10.0.0.1:8000/api/v1")
        store.putString(SecretKeys.DEVICE_TOKEN, "tok")
        return SecureSettingsRepository(store)
    }

    @Test
    fun `sin configuracion muestra primer arranque`() {
        val store = InMemorySecretStore()
        val repo = SecureSettingsRepository(store)

        composeRule.setContent {
            WakemeupTheme {
                AppRoot(settings = repo, client = okMachinesApi())
            }
        }

        composeRule.onNodeWithText("Configurar").assertIsDisplayed()
        composeRule.onNodeWithText("Conecta con tu red").assertIsDisplayed()
    }

    @Test
    fun `con configuracion guardada va directo al listado`() {
        val store = InMemorySecretStore()
        val repo = configure(store)

        composeRule.setContent {
            WakemeupTheme {
                AppRoot(settings = repo, client = okMachinesApi())
            }
        }

        // El listado se muestra (ni primer arranque, ni ajustes): la fila existe.
        composeRule.onNodeWithText("Desktop").assertIsDisplayed()
    }

    @Test
    fun `401 revocado limpia config y redirige a ajustes`() {
        val store = InMemorySecretStore()
        val repo = configure(store)

        composeRule.setContent {
            WakemeupTheme {
                AppRoot(settings = repo, client = revokedApi())
            }
        }

        // El fetch de arranque recibe 401 → sesión inválida → ajustes.
        composeRule.waitForIdle()
        composeRule.onNodeWithText("Ajustes").assertIsDisplayed()
        composeRule.onNodeWithText("Guardar y conectar").assertIsDisplayed()
        // La configuración se ha limpiado.
        assertThat(store.getString(SecretKeys.DEVICE_TOKEN)).isNull()
    }

    @Test
    fun `guardar con exito navega al listado`() {
        val store = InMemorySecretStore()
        val repo = SecureSettingsRepository(store)

        composeRule.setContent {
            WakemeupTheme {
                AppRoot(settings = repo, client = okMachinesApi())
            }
        }

        // Primer arranque → Configurar → ajustes → guardar con BE que responde OK.
        composeRule.onNodeWithText("Configurar").performClick()
        composeRule.onNodeWithText("Dirección del servidor").assertIsDisplayed()

        composeRule.onNodeWithTag("settings_url_field").performTextInput("http://10.0.0.1:8000")
        composeRule.onNodeWithTag("settings_token_field").performTextInput("tok")
        composeRule.onNodeWithTag("settings_save_button").performClick()

        // Validación OK → persiste → navega al listado (fila visible).
        composeRule.waitForIdle()
        composeRule.onNodeWithText("Desktop").assertIsDisplayed()
        assertThat(store.getString(SecretKeys.API_URL)).isEqualTo("http://10.0.0.1:8000/api/v1")
    }

    @Test
    fun `ajustes accesibles desde el listado`() {
        val store = InMemorySecretStore()
        val repo = configure(store)

        composeRule.setContent {
            WakemeupTheme {
                AppRoot(settings = repo, client = okMachinesApi())
            }
        }
        composeRule.onNodeWithText("Desktop").assertIsDisplayed()

        // Botón de ajustes en el header del listado → pantalla de ajustes.
        composeRule.onNodeWithContentDescription("Abrir ajustes").performClick()
        composeRule.onNodeWithText("Dirección del servidor").assertIsDisplayed()

        // Volver desde ajustes (entrados desde el listado) regresa al listado,
        // no al primer arranque (blind-hunter 1.6).
        composeRule.onNodeWithContentDescription("Volver").performClick()
        composeRule.onNodeWithText("Desktop").assertIsDisplayed()
    }

    @Test
    fun `prompt REQUEST muestra el dialogo de racional del permiso`() {
        val store = InMemorySecretStore()
        val repo = configure(store)

        composeRule.setContent {
            WakemeupTheme {
                AppRoot(settings = repo, client = okMachinesApi())
            }
        }
        composeRule.onNodeWithText("Desktop").assertIsDisplayed()

        fakeNotifier.emit(NotificationPermissionPrompt.REQUEST)
        composeRule.waitForIdle()

        // FR-18: se pide POST_NOTIFICATIONS con explicación al primer evento mcp.
        composeRule.onNodeWithText("Activar notificaciones").assertIsDisplayed()
        composeRule.onNodeWithText("Ahora no").assertIsDisplayed()
    }

    @Test
    fun `prompt SETTINGS muestra el aviso de activarlas en ajustes`() {
        val store = InMemorySecretStore()
        val repo = configure(store)

        composeRule.setContent {
            WakemeupTheme {
                AppRoot(settings = repo, client = okMachinesApi())
            }
        }
        composeRule.onNodeWithText("Desktop").assertIsDisplayed()

        fakeNotifier.emit(NotificationPermissionPrompt.SETTINGS)
        composeRule.waitForIdle()

        // Permiso ya denegado: no se vuelve a pedir; se sugiere ajustes.
        assertThat(ShadowToast.getTextOfLatestToast())
            .contains("Notificaciones desactivadas")
    }
}
