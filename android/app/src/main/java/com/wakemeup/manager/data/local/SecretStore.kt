package com.wakemeup.manager.data.local

/**
 * Almacén cifrado de secretos (AD-8, FR-15): la URL y el token de dispositivo
 * viven en un almacén seguro (Keystore + encriptación), nunca en SharedPreferences
 * planas ni en texto plano.
 *
 * Interfaz deliberadamente mínima y síncrona (lecturas en ms, sin splash):
 * [SecureSettingsRepository] la consume para la caché en memoria; impl real
 * sobre el Keystore, doble en memoria para tests (Robolectric no simula el
 * Keystore de forma fiable).
 */
interface SecretStore {
    fun getString(key: String): String?
    fun putString(key: String, value: String)
    fun remove(key: String)
}
