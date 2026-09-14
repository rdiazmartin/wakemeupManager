package com.wakemeup.manager.data.local

import com.google.common.truth.Truth.assertThat
import kotlinx.coroutines.test.runTest
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Cobertura Robolectric del almacenamiento seguro (story 1.6, AD-8/FR-15):
 * round-trip save/load sobre el [SecretStore], clear, y normalización de la
 * URL base (OQ-2). La garantía de "sin SharedPreferences planas" la aporta la
 * impl real [KeystoreSecretStore] (EncryptedSharedPreferences + MasterKey);
 * aquí se testea el comportamiento del repositorio sobre el contrato del
 * almacén (los tests usan doble en memoria para no depender del Keystore).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34])
class SecureSettingsRepositoryTest {

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

    private fun subject(store: SecretStore): SecureSettingsRepository =
        SecureSettingsRepository(store)

    @Test
    fun `save y load hacen round trip de url y token`() = runTest {
        val store = InMemorySecretStore()
        val repo = subject(store)
        assertThat(repo.load()).isFalse()

        repo.save("http://10.0.0.1:8000", "tok-secreto")
        assertThat(repo.apiUrl).isEqualTo("http://10.0.0.1:8000/api/v1")
        assertThat(repo.deviceToken).isEqualTo("tok-secreto")

        // Un repositorio nuevo sobre el MISMO almacén (simula re-arranque:
        // la caché en memoria parte de cero y se repuebla desde el almacén).
        val reloaded = subject(store)
        assertThat(reloaded.load()).isTrue()
        assertThat(reloaded.apiUrl).isEqualTo("http://10.0.0.1:8000/api/v1")
        assertThat(reloaded.deviceToken).isEqualTo("tok-secreto")
    }

    @Test
    fun `clear deja sin config`() = runTest {
        val repo = subject(InMemorySecretStore())
        repo.save("http://h:8000", "t")
        assertThat(repo.load()).isTrue()

        repo.clear()
        assertThat(repo.load()).isFalse()
    }

    @Test
    fun `normalizacion de URL base`() {
        val repo = subject(InMemorySecretStore())
        // URL base → + /api/v1 (OQ-2).
        assertThat(repo.normalizedApiUrl("http://10.0.0.1:8000"))
            .isEqualTo("http://10.0.0.1:8000/api/v1")
        // Slash final quitado.
        assertThat(repo.normalizedApiUrl("http://10.0.0.1:8000/"))
            .isEqualTo("http://10.0.0.1:8000/api/v1")
        // Idempotente con /api/v1 ya presente.
        assertThat(repo.normalizedApiUrl("http://10.0.0.1:8000/api/v1"))
            .isEqualTo("http://10.0.0.1:8000/api/v1")
        // https también vale.
        assertThat(repo.normalizedApiUrl("  https://be:8443  "))
            .isEqualTo("https://be:8443/api/v1")
        // In-válidos.
        assertThat(repo.normalizedApiUrl("10.0.0.1:8000")).isNull()
        assertThat(repo.normalizedApiUrl("ftp://h")).isNull()
        assertThat(repo.normalizedApiUrl("")).isNull()
        assertThat(repo.normalizedApiUrl("http://")).isNull()
    }

    @Test
    fun `save rechaza URL invalida`() = runTest {
        val repo = subject(InMemorySecretStore())
        var thrown: IllegalArgumentException? = null
        try {
            repo.save("nope", "t")
        } catch (e: IllegalArgumentException) {
            thrown = e
        }
        assertThat(thrown).isNotNull()
    }
}
