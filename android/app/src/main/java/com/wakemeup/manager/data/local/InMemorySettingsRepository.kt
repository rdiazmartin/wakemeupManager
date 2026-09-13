package com.wakemeup.manager.data.local

import com.wakemeup.manager.BuildConfig

/**
 * Implementación en memoria alimentada por `BuildConfig` (decisión de usuario para 1.5).
 * La URL y el token de ejemplo se sobreescriben en la compilación real del dispositivo.
 */
class InMemorySettingsRepository(
    override val apiUrl: String = BuildConfig.WAKEMEUP_API_URL,
    override val deviceToken: String = BuildConfig.WAKEMEUP_DEVICE_TOKEN,
) : SettingsRepository
