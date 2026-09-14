package com.wakemeup.manager.data.local

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences.PrefKeyEncryptionScheme
import androidx.security.crypto.EncryptedSharedPreferences.PrefValueEncryptionScheme
import androidx.security.crypto.MasterKey

/** Claves del almacén seguro (no usar fuera de [KeystoreSecretStore]). */
object SecretKeys {
    const val API_URL = "api_url"
    const val DEVICE_TOKEN = "device_token"
}

/**
 * Impl real de [SecretStore]: EncryptedSharedPreferences (security-crypto 1.1.0,
 * no deprecada en esta versión — verificada con javap) protegida por un
 * MasterKey AES256_GCM del Keystore de Android (FR-15, AD-8).
 *
 * Nota de 1.6 (almacenamiento seguro): `EncryptedSharedPreferences` se excluyó
 * del spec por su deprecación anunciada, pero en `security-crypto 1.1.0` la
 * clase NO está deprecada y es la vía canónica sin dependencias extra; si una
 * versión futura la deprecara, migrar a EncryptedFile+DataStore sin tocar el
 * contrato de [SecretStore].
 */
class KeystoreSecretStore(context: Context) : SecretStore {

    private val prefs: SharedPreferences = run {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            FILE,
            masterKey,
            PrefKeyEncryptionScheme.AES256_SIV,
            PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    override fun getString(key: String): String? =
        prefs.getString(key, null)

    override fun putString(key: String, value: String) {
        // commit() (síncrono) en lugar de apply(): la escritura del token es un
        // momento crítico (primer arranque / reconfiguración); un apply() con
        // muerte del proceso a medio flush perdería la sesión recién guardada.
        prefs.edit().putString(key, value).commit()
    }

    override fun remove(key: String) {
        prefs.edit().remove(key).commit()
    }

    private companion object {
        const val FILE = "wakemeup_secure"
    }
}
