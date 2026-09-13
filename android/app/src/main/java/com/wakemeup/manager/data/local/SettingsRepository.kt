package com.wakemeup.manager.data.local

/**
 * Origen de la URL y el token del BE (decisión de usuario para la story 1.5).
 *
 * La story 1.6 conectará esta misma interfaz a Keystore + Encrypted DataStore,
 * sin tocar el ViewModel ni la capa remota.
 */
interface SettingsRepository {
    val apiUrl: String
    val deviceToken: String
}
