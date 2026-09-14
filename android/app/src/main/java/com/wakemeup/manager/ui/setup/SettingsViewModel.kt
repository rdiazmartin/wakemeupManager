package com.wakemeup.manager.ui.setup

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.wakemeup.manager.data.local.SettingsRepository
import com.wakemeup.manager.data.local.SecureSettingsRepository
import com.wakemeup.manager.data.local.UrlNormalizer
import com.wakemeup.manager.data.remote.ApiException
import com.wakemeup.manager.data.remote.WakemeupApi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Estado del formulario de ajustes (UDF, AD-8). */
data class SettingsUiState(
    val apiUrlBase: String = "",
    val deviceToken: String = "",
    /** true mientras se valida/guarda contra el BE (guard: nunca dos en vuelo). */
    val isSaving: Boolean = false,
    /** Error de validación local (formato de URL) al pulsar guardar. */
    val formatError: Boolean = false,
    /** 401 del BE: token rechazado (UX-DR9). */
    val tokenRejected: Boolean = false,
    /** Timeout/red: BE inalcanzable (UX-DR9). */
    val backendUnreachable: Boolean = false,
) {
    val errorMessage: Boolean get() = formatError || tokenRejected || backendUnreachable
}

/**
 * ViewModel de la pantalla de ajustes (story 1.6): valida la URL localmente,
 * valida URL+token contra `GET /machines` (decisión del usuario, OQ-1) y, si
 * es correcto, persiste en el almacén seguro y avisa para navegar al listado.
 * No guarda NUNCA con 401 o sin conexión.
 */
class SettingsViewModel(
    private val settings: SettingsRepository,
    private val api: WakemeupApi,
    private val normalizeUrl: (String) -> String? = { raw ->
        (settings as? SecureSettingsRepository)?.normalizedApiUrl(raw)
            ?: defaultNormalizer(raw)
    },
    /** Ámbito inyectable para los tests de Robolectric; en producción usa [ViewModel.viewModelScope]. */
    private val externalScope: CoroutineScope? = null,
) : ViewModel() {

    private val scope: CoroutineScope = externalScope ?: viewModelScope

    private val _uiState = MutableStateFlow(SettingsUiState())
    val uiState: StateFlow<SettingsUiState> = _uiState.asStateFlow()

    private val _saved = MutableStateFlow(false)
    /** true tras guardar con éxito (MainActivity navega al listado). */
    val saved: StateFlow<Boolean> = _saved.asStateFlow()

    fun onUrlChanged(value: String) {
        _uiState.update {
            it.copy(
                apiUrlBase = value,
                formatError = false,
                tokenRejected = false,
                backendUnreachable = false,
            )
        }
    }

    fun onTokenChanged(value: String) {
        _uiState.update {
            it.copy(
                deviceToken = value,
                tokenRejected = false,
                backendUnreachable = false,
            )
        }
    }

    /**
     * Guardar: valida el formato (sin llamar al BE), después valida contra
     * `GET /machines` y solo persiste si responde 2xx (decisiones 1.6:
     * OQ-1 validar contra endpoint autenticado, OQ-2 URL base normalizada).
     */
    fun save() {
        if (_uiState.value.isSaving) return  // guard: un guardado en vuelo como máximo
        val state = _uiState.value
        val normalized = normalizeUrl(state.apiUrlBase)
        if (normalized == null || state.deviceToken.isBlank()) {
            _uiState.update {
                it.copy(
                    formatError = true,
                    tokenRejected = false,
                    backendUnreachable = false,
                )
            }
            return
        }
        _uiState.update { it.copy(isSaving = true, formatError = false, tokenRejected = false, backendUnreachable = false) }
        scope.launch {
            try {
                // Valida las credenciales INTRODUCIDAS (URL normalizada + token),
                // no la caché del repositorio (vacía en primer arranque).
                api.validateConnection(normalized, state.deviceToken.trim())
                settings.save(normalized, state.deviceToken.trim())
                _saved.value = true
            } catch (e: ApiException) {
                // 401 (token rechazado) vs resto de errores HTTP del BE.
                if (e.code == "unauthorized") {
                    _uiState.update { it.copy(isSaving = false, tokenRejected = true) }
                } else {
                    _uiState.update { it.copy(isSaving = false, backendUnreachable = true) }
                }
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e  // el guardado se canceló (navegación/cierre): no tragar
            } catch (_: Exception) {
                // Timeout, red caída, DNS: BE inalcanzable (UX-DR9).
                _uiState.update { it.copy(isSaving = false, backendUnreachable = true) }
            }
        }
    }

    /** Al entrar en la pantalla: limpia el flag de guardado previo para no
     *  auto-navegar al reabrir ajustes con las mismas credenciales. */
    fun resetSaved() {
        _saved.value = false
    }

    companion object {
        /** Normalización única de URL base (decisión 1.6, OQ-2): [UrlNormalizer]. */
        internal fun defaultNormalizer(raw: String): String? = UrlNormalizer.apiUrlFromBase(raw)

        fun createFactory(
            settings: SettingsRepository,
            api: WakemeupApi,
        ): ViewModelProvider.Factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T = when {
                modelClass.isAssignableFrom(SettingsViewModel::class.java) ->
                    SettingsViewModel(settings = settings, api = api) as T
                else -> throw IllegalArgumentException("Unknown ViewModel class ${modelClass.name}")
            }
        }

        /** Constructor de test sin navegación. */
        fun createForTest(
            settings: SettingsRepository,
            api: WakemeupApi,
            normalizeUrl: (String) -> String? = ::defaultNormalizer,
            externalScope: CoroutineScope,
        ): SettingsViewModel = SettingsViewModel(settings, api, normalizeUrl, externalScope)
    }
}
