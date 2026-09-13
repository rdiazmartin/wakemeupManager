package com.wakemeup.manager.ui.machines

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.wakemeup.manager.data.local.MachinesCache
import com.wakemeup.manager.data.remote.ApiException
import com.wakemeup.manager.data.remote.WakemeupApi
import com.wakemeup.manager.domain.Machine
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** Estado único de la pantalla de listado (UDF, AD-8). */
data class MachineListUiState(
    /** true solo durante el primer fetch (skeleton 4-6 filas). */
    val isLoading: Boolean = true,
    val machines: List<Machine> = emptyList(),
    val isEmpty: Boolean = false,
    /** Último fetch falló con caché disponible (badge "Sin conexión"). */
    val isOffline: Boolean = false,
    /** POST /scan en vuelo (spinner / texto Escaneando… con Reduce Motion). */
    val isScanning: Boolean = false,
)

class MachineListViewModel(
    private val api: WakemeupApi,
    private val cache: MachinesCache,
    /** Período de polling en ms (10–300 s; default 30 s). */
    private val pollingIntervalMs: Long = DEFAULT_POLL_INTERVAL_MS,
    private val timeProvider: () -> Long = System::currentTimeMillis,
    /** Ámbito inyectable para los tests de Robolectric; en producción usa [ViewModel.viewModelScope]. */
    private val externalScope: CoroutineScope? = null,
) : ViewModel() {

    private val scope: CoroutineScope = externalScope ?: viewModelScope

    private val _uiState = MutableStateFlow(MachineListUiState())
    val uiState: StateFlow<MachineListUiState> = _uiState.asStateFlow()

    private var pollJob: Job? = null
    private var startedAt: Long = 0L

    fun start() {
        if (pollJob != null) return
        startedAt = timeProvider()
        pollJob = scope.launch {
            refresh()
            while (true) {
                val elapsed = timeProvider() - startedAt
                delay(pollingIntervalMs - (elapsed % pollingIntervalMs))
                refresh(silent = true)
            }
        }
    }

    fun refresh(silent: Boolean = false) = withScopeOrNothing {
        runCatching { api.listMachines() }
            .onSuccess { machines ->
                cache.save(machines)
                _uiState.update {
                    it.copy(
                        isLoading = false,
                        machines = machines,
                        isEmpty = machines.isEmpty(),
                        isOffline = false,
                    )
                }
            }
            .onFailure { error ->
                val cached = cache.get()
                _uiState.update {
                    it.copy(
                        isLoading = false,
                        // Caché en memoria: muestra la última lista OK si existe (UX-DR4).
                        machines = if (cached.isNotEmpty()) cached else it.machines,
                        isEmpty = cached.isEmpty() && it.machines.isEmpty(),
                        isOffline = cached.isNotEmpty() || it.machines.isNotEmpty(),
                    )
                }
            }
    }

    /** "Escanear ahora": POST /scan, spinner mientras escanea y refresco tras el 202 (AD-3). */
    fun scanNow() = withScopeOrNothing {
        _uiState.update { it.copy(isScanning = true) }
        runCatching { api.triggerScan() }
        _uiState.update { it.copy(isScanning = false) }
        refresh()
    }

    /**
     * Ahorro de batería: el polling se pausa (NFR-10); el flujo se reanuda al salir.
     * El refresco manual y el primer fetch siguen funcionando mientras haya batería baja.
     */
    fun setBatterySaver(enabled: Boolean) {
        if (enabled) {
            pollJob?.cancel()
            pollJob = null
        } else {
            start()
        }
    }

    private inline fun withScopeOrNothing(crossinline block: suspend () -> Unit) {
        if (scope.isActive) scope.launch { block() }
    }

    companion object {
        const val DEFAULT_POLL_INTERVAL_MS = 30_000L

        /** Factory que cablea la red (Ktor + BuildConfig) y la caché en memoria. */
        fun createFactory(
            api: WakemeupApi = WakemeupApi(com.wakemeup.manager.data.local.InMemorySettingsRepository()),
            cache: MachinesCache = MachinesCache(),
        ): ViewModelProvider.Factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T = when {
                modelClass.isAssignableFrom(MachineListViewModel::class.java) ->
                    MachineListViewModel(api = api, cache = cache) as T
                else -> throw IllegalArgumentException("Unknown ViewModel class ${modelClass.name}")
            }
        }

        fun isBatterySaverActive(context: Context): Boolean {
            val pm = context.getSystemService(Context.POWER_SERVICE)
            return (pm as? android.os.PowerManager)?.isPowerSaveMode ?: false
        }

        fun isBatteryLow(context: Context): Boolean {
            val action = Intent(Intent.ACTION_BATTERY_CHANGED)
            val sticky = context.registerReceiver(null, IntentFilter(action.action)) ?: return false
            val level = sticky.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
            val scale = sticky.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
            if (level < 0 || scale <= 0) return false
            val pct = level * 100 / scale
            return pct <= 15
        }
    }
}
