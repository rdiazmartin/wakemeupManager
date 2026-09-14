package com.wakemeup.manager.ui.setup

import com.google.common.truth.Truth.assertThat
import com.wakemeup.manager.data.local.SecureSettingsRepository
import com.wakemeup.manager.data.local.SecretKeys
import com.wakemeup.manager.data.local.SecretStore
import com.wakemeup.manager.data.local.SettingsRepository
import com.wakemeup.manager.data.remote.WakemeupApi
import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.MockEngineConfig
import io.ktor.client.engine.mock.respond
import io.ktor.client.plugins.contentnegotiation.ContentNegotiation
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import io.ktor.serialization.kotlinx.json.json
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Cobertura Robolectric del ViewModel de ajustes (requisito transversal de
 * tests de la story 1.6): validación de formato sin llamar al BE, validación
 * contra GET /machines (401 → token rechazado, timeout/red → BE inalcanzable),
 * guardado seguro y normalización de la URL base (decisiones OQ-1/OQ-2).
 */
@OptIn(ExperimentalCoroutinesApi::class)
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class SettingsViewModelTest {

    /** Almacén en memoria (doble de [SecretStore] para no depender del Keystore). */
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

    private fun TestScope.settingsApi(handler: io.ktor.client.engine.mock.MockRequestHandler): WakemeupApi {
        val config = MockEngineConfig().apply {
            dispatcher = StandardTestDispatcher(testScheduler)
            addHandler(handler)
        }
        val client = HttpClient(MockEngine(config)) {
            expectSuccess = false
            install(ContentNegotiation) {
                json(WakemeupApi.defaultJson())
            }
        }
        return WakemeupApi(
            settings = SecureSettingsRepository(InMemorySecretStore().also {
                it.putString(SecretKeys.API_URL, "http://test/api/v1")
                it.putString(SecretKeys.DEVICE_TOKEN, "t")
            }),
            client = client,
        )
    }

    private fun TestScope.okApi(): WakemeupApi = settingsApi { request ->
        respond(
            """{"machines":[]}""",
            HttpStatusCode.OK,
            headersOf(HttpHeaders.ContentType, "application/json"),
        )
    }

    private fun TestScope.unauthorizedApi(): WakemeupApi = settingsApi {
        respond(
            """{"error":{"code":"unauthorized","message":"token inválido"}}""",
            HttpStatusCode.Unauthorized,
            headersOf(HttpHeaders.ContentType, "application/json"),
        )
    }

    private fun TestScope.unreachableApi(): WakemeupApi = settingsApi {
        throw java.net.ConnectException("no route to host")
    }

    @Test
    fun `guardar valido persiste y marca saved`() = runTest {
        val store = InMemorySecretStore()
        val settings = SecureSettingsRepository(store)
        val vm = SettingsViewModel(
            settings = settings,
            externalScope = backgroundScope,
            api = okApi(),
            normalizeUrl = SecureSettingsRepository(store)::normalizedApiUrl,
        )

        vm.onUrlChanged("http://10.0.0.1:8000")
        vm.onTokenChanged(" tok123 ")
        vm.save()
        runCurrent()

        assertThat(vm.saved.value).isTrue()
        // Guardado normalizado + token sin espacios (OQ-2).
        assertThat(settings.apiUrl).isEqualTo("http://10.0.0.1:8000/api/v1")
        assertThat(settings.deviceToken).isEqualTo("tok123")
        assertThat(store.getString(SecretKeys.API_URL)).isEqualTo("http://10.0.0.1:8000/api/v1")
        assertThat(store.getString(SecretKeys.DEVICE_TOKEN)).isEqualTo("tok123")
    }

    @Test
    fun `401 no guarda y muestra token rechazado`() = runTest {
        val store = InMemorySecretStore()
        val settings = SecureSettingsRepository(store)
        val vm = SettingsViewModel(
            settings = settings,
            externalScope = backgroundScope,
            api = unauthorizedApi(),
            normalizeUrl = SecureSettingsRepository(store)::normalizedApiUrl,
        )

        vm.onUrlChanged("http://10.0.0.1:8000")
        vm.onTokenChanged("abc")
        vm.save()
        runCurrent()

        assertThat(vm.saved.value).isFalse()
        assertThat(vm.uiState.value.tokenRejected).isTrue()
        assertThat(vm.uiState.value.errorMessage).isTrue()
        // Nada se persiste con token rechazado.
        assertThat(store.getString(SecretKeys.DEVICE_TOKEN)).isNull()
    }

    @Test
    fun `timeout de red no guarda y muestra BE inalcanzable`() = runTest {
        val store = InMemorySecretStore()
        val settings = SecureSettingsRepository(store)
        val vm = SettingsViewModel(
            settings = settings,
            externalScope = backgroundScope,
            api = unreachableApi(),
            normalizeUrl = SecureSettingsRepository(store)::normalizedApiUrl,
        )

        vm.onUrlChanged("http://10.0.0.1:8000")
        vm.onTokenChanged("abc")
        vm.save()
        runCurrent()

        assertThat(vm.saved.value).isFalse()
        assertThat(vm.uiState.value.backendUnreachable).isTrue()
        assertThat(store.getString(SecretKeys.DEVICE_TOKEN)).isNull()
    }

    @Test
    fun `error HTTP no 401 tampoco guarda`() = runTest {
        // El BE responde 500 (envelope uniforme): no es "token rechazado" pero
        // tampoco se guarda nunca una sesión no validada (verification-gap 1.6).
        val store = InMemorySecretStore()
        val settings = SecureSettingsRepository(store)
        val vm = SettingsViewModel(
            settings = settings,
            externalScope = backgroundScope,
            api = settingsApi {
                respond(
                    """{"error":{"code":"internal_error","message":"boom"}}""",
                    HttpStatusCode.InternalServerError,
                    headersOf(HttpHeaders.ContentType, "application/json"),
                )
            },
            normalizeUrl = SecureSettingsRepository(store)::normalizedApiUrl,
        )

        vm.onUrlChanged("http://10.0.0.1:8000")
        vm.onTokenChanged("abc")
        vm.save()
        runCurrent()

        assertThat(vm.saved.value).isFalse()
        assertThat(vm.uiState.value.backendUnreachable).isTrue()
        assertThat(store.getString(SecretKeys.DEVICE_TOKEN)).isNull()
    }

    @Test
    fun `URL invalida da error de formato sin llamar al BE`() = runTest {
        var beHits = 0
        val api = settingsApi {
            beHits++
            respond(
                """{"machines":[]}""",
                HttpStatusCode.OK,
                headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }
        val store = InMemorySecretStore()
        val vm = SettingsViewModel(
            settings = SecureSettingsRepository(store),
            externalScope = backgroundScope,
            api = api,
            normalizeUrl = SecureSettingsRepository(store)::normalizedApiUrl,
        )

        vm.onUrlChanged("10.0.0.1:8000") // sin esquema
        vm.onTokenChanged("abc")
        vm.save()
        runCurrent()

        assertThat(vm.uiState.value.formatError).isTrue()
        assertThat(vm.saved.value).isFalse()
        assertThat(beHits).isEqualTo(0)
    }

    @Test
    fun `token vacio da error de formato`() = runTest {
        val store = InMemorySecretStore()
        val vm = SettingsViewModel(
            settings = SecureSettingsRepository(store),
            externalScope = backgroundScope,
            api = okApi(),
            normalizeUrl = SecureSettingsRepository(store)::normalizedApiUrl,
        )

        vm.onUrlChanged("http://10.0.0.1:8000")
        vm.onTokenChanged("   ")
        vm.save()
        runCurrent()

        assertThat(vm.uiState.value.formatError).isTrue()
        assertThat(vm.saved.value).isFalse()
    }

    @Test
    fun `doble tap en guardar solo valida una vez`() = runTest {
        var beHits = 0
        val api = settingsApi {
            beHits++
            respond(
                """{"machines":[]}""",
                HttpStatusCode.OK,
                headersOf(HttpHeaders.ContentType, "application/json"),
            )
        }
        val store = InMemorySecretStore()
        val vm = SettingsViewModel(
            settings = SecureSettingsRepository(store),
            externalScope = backgroundScope,
            api = api,
            normalizeUrl = SecureSettingsRepository(store)::normalizedApiUrl,
        )

        vm.onUrlChanged("http://10.0.0.1:8000")
        vm.onTokenChanged("abc")
        vm.save()
        vm.save()
        runCurrent()

        assertThat(beHits).isEqualTo(1)
        assertThat(vm.saved.value).isTrue()
    }
}
