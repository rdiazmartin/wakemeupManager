package com.wakemeup.manager.data.local

import android.content.Context

/**
 * Dónde viven la URL y el token del BE (decisión de usuario para 1.5/1.6).
 *
 * Los `val` síncronos se leen de la caché en memoria (poblada al arrancar);
 * [save] persiste de forma segura (Keystore + cifrado, AD-8) y [clear] borra
 * la sesión (401 revocado). `WakemeupApi` y `MachineListViewModel` consumen
 * solo los `val`, sin cambios de firma.
 */
interface SettingsRepository {
    val apiUrl: String
    val deviceToken: String

    /**
     * Persiste la configuración de conexión (URL + token) en el almacén seguro
     * y actualiza la caché en memoria. Lanza [IllegalArgumentException] si la
     * URL no es http(s) o está vacía.
     */
    suspend fun save(apiUrl: String, deviceToken: String)

    /** Borra la configuración (401 revocado / restablecer). */
    suspend fun clear()

    /**
     * Carga la configuración persistida en la caché en memoria.
     * Devuelve true si había configuración guardada.
     */
    suspend fun load(): Boolean

    companion object {
        /** Crea la implementación segura de producción. */
        fun secure(context: Context): SettingsRepository =
            SecureSettingsRepository(KeystoreSecretStore(context.applicationContext))
    }
}
