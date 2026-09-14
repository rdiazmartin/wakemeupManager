package com.wakemeup.manager.data.local

/**
 * Implementación segura de [SettingsRepository] (story 1.6, AD-8/FR-15):
 * los `val` expuestos son una caché en memoria poblada por [load] al arrancar
 * y por [save] al guardar; la persistencia va al [SecretStore] cifrado (Keystore).
 *
 * Proxy síncrono deliberado: `WakemeupApi` lee `apiUrl`/`deviceToken` como val
 * en cada petición sin cambiar su firma (decisión 1.5/1.6).
 */
class SecureSettingsRepository(private val store: SecretStore) : SettingsRepository {

    @Volatile
    private var cachedUrl: String = ""

    @Volatile
    private var cachedToken: String = ""

    override val apiUrl: String get() = cachedUrl
    override val deviceToken: String get() = cachedToken

    override suspend fun save(apiUrl: String, deviceToken: String) {
        val normalized = normalizedApiUrl(apiUrl)
        requireNotNull(normalized) { "URL no válida para la conexión: $apiUrl" }
        store.putString(SecretKeys.API_URL, normalized)
        store.putString(SecretKeys.DEVICE_TOKEN, deviceToken.trim())
        cachedUrl = normalized
        cachedToken = deviceToken.trim()
    }
    override suspend fun clear() {
        store.remove(SecretKeys.API_URL)
        store.remove(SecretKeys.DEVICE_TOKEN)
        cachedUrl = ""
        cachedToken = ""
    }

    override suspend fun load(): Boolean {
        val url = store.getString(SecretKeys.API_URL)
        val token = store.getString(SecretKeys.DEVICE_TOKEN)
        cachedUrl = url ?: ""
        cachedToken = token ?: ""
        return !cachedUrl.isNullOrEmpty() && !cachedToken.isNullOrEmpty()
    }

    /**
     * Normaliza la URL base que escribe el usuario (decisión 1.6, OQ-2):
     * `http://h:8000` → `http://h:8000/api/v1` (idempotente). Lógica única en
     * [UrlNormalizer]; este delegado existe para no duplicarla en el ViewModel.
     */
    internal fun normalizedApiUrl(raw: String): String? = UrlNormalizer.apiUrlFromBase(raw)
}
