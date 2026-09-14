package com.wakemeup.manager.data.local

/**
 * Normalización única de la URL base que introduce el usuario en los ajustes
 * (decisión 1.6, OQ-2): `http://h:8000` → `http://h:8000/api/v1`.
 *
 * - Quita espacios y el `/` final.
 * - Exige esquema `http://` o `https://` con host no vacío (puerto libre).
 * - Añade `/api/v1` si no termina ya en él (idempotente, incl. variantes de
 *   caja del prefijo).
 * - Devuelve null si el formato no es válido.
 */
object UrlNormalizer {
    fun apiUrlFromBase(raw: String): String? {
        val trimmed = raw.trim()
        if (trimmed.isEmpty()) return null
        val schemeEnd = trimmed.indexOf("://")
        if (schemeEnd <= 0) return null
        val scheme = trimmed.substring(0, schemeEnd)
        if (scheme != "http" && scheme != "https") return null
        val withoutScheme = trimmed.substring(schemeEnd + 3)
        if (withoutScheme.isEmpty()) return null
        val host = withoutScheme.substringBefore('/').substringBefore(':')
        if (host.isEmpty()) return null

        // Quita el / final (y el prefijo /api/v1 en cualquier caja, idempotente).
        var base = trimmed.trimEnd('/')
        val apiSuffix = Regex("(?i)/api/v1$")
        if (apiSuffix.containsMatchIn(base)) {
            base = apiSuffix.replaceFirst(base, "")
        }
        return "$base/api/v1"
    }
}
