package com.wakemeup.manager.data.local

import com.wakemeup.manager.BuildConfig

/**
 * Implementación en memoria alimentada por `BuildConfig` (decisión de usuario
 * para 1.5). Tras la story 1.6 queda EXCLUSIVAMENTE como doble de test: la
 * producción usa [SecureSettingsRepository]; los métodos de persistencia
 * lanzan porque este doble no persiste nada.
 */
class InMemorySettingsRepository(
    override val apiUrl: String = BuildConfig.WAKEMEUP_API_URL,
    override val deviceToken: String = BuildConfig.WAKEMEUP_DEVICE_TOKEN,
) : SettingsRepository {

    override suspend fun save(apiUrl: String, deviceToken: String) =
        throw UnsupportedOperationException("InMemorySettingsRepository no persiste (doble de test)")

    override suspend fun clear() =
        throw UnsupportedOperationException("InMemorySettingsRepository no persiste (doble de test)")

    override suspend fun load(): Boolean =
        throw UnsupportedOperationException("InMemorySettingsRepository no persiste (doble de test)")
}
