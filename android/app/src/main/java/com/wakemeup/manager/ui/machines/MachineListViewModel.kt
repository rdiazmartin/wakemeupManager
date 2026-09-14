package com.wakemeup.manager.ui.machines

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.SystemClock
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
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
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
    /** Último fetch o scan falló (badge "Sin conexión"). */
    val isOffline: Boolean = false,
    /** POST /scan en vuelo (spinner / texto Escaneando… con Reduce Motion). */
    val isScanning: Boolean = false,
    /** Pull-to-refresh atado a la operación real (no parpadea). */
    val isRefreshing: Boolean = false,
)

class MachineListViewModel(
    private val api: WakemeupApi,
    private val cache: MachinesCache,
    /** Período de polling en ms (10–300 s; default 30 s). */
    private val pollingIntervalMs: Long = DEFAULT_POLL_INTERVAL_MS,
    /** Reloj monotónico por defecto (immune a saltos de reloj de pared). */
    private val timeProvider: () -> Long = { SystemClock.elapsedRealtime() },
    /** Ámbito inyectable para los tests de Robolectric; en producción usa [ViewModel.viewModelScope]. */
    private val externalScope: CoroutineScope? = null,
) : ViewModel() {

    private val scope: CoroutineScope = externalScope ?: viewModelScope

    private val _uiState = MutableStateFlow(MachineListUiState())
    val uiState: StateFlow<MachineListUiState> = _uiState.asStateFlow()

    /**
     * Sesión inválida (401 del BE, story 1.6): se emite cuando un refresh o un
     * escaneo recibe `unauthorized` (envelope 1.4) — nunca el badge de offline,
     * que queda para fallos de red. `MainActivity` la escucha y redirige a
     * ajustes. Replay=1: si el colector aún no está suscrito (o un 401 llega
     * justo antes de suscribirse en la recreación del ViewModel), el evento
     * no se pierde.
     */
    private val _sessionInvalid = MutableSharedFlow<Unit>(replay = 1)
    val sessionInvalid: SharedFlow<Unit> = _sessionInvalid.asSharedFlow()

    /**
     * Detiene el polling y cancela los jobs en vuelo. `MainActivity` la usa
     * al abandonar la pantalla (navegación a ajustes) para que el ViewModel
     * no siga pidiendo contra una sesión revocada/vacía.
     */
    fun stop() {
        pollJob?.cancel()
        pollJob = null
        refreshJob?.cancel()
        refreshJob = null
    }

    /**
     * Elimina el replay del evento de sesión inválida tras ser manejado:
     * si el usuario re-guarda las mismas credenciales y el ViewModel se
     * reutiliza (misma key), un 401 antiguo re-jugado provocaría un bucle
     * de redirección a ajustes.
     */
    fun sessionInvalidHandled() {
        _sessionInvalid.resetReplayCache()
    }

    private var pollJob: Job? = null
    /** Serializa los refrescos: cada nueva llamada cancela el anterior (último gana). */
    private var refreshJob: Job? = null
    private var startedAt: Long = 0L

    init {
        require(pollingIntervalMs in 10_000..300_000) {
            "pollingIntervalMs debe estar en 10 s..300 s (FR-12), dado $pollingIntervalMs"
        }
    }

    fun start() {
        if (pollJob != null) return
        startedAt = timeProvider()
        pollJob = scope.launch {
            refresh()
            while (true) {
                val elapsed = timeProvider() - startedAt
                // Módulo seguro: si el reloj salta hacia atrás (NTP/mano),
                // el tick no se vuelve negativo; con elapsedRealtime no ocurre.
                val tick = (elapsed % pollingIntervalMs + pollingIntervalMs) % pollingIntervalMs
                delay(pollingIntervalMs - tick)
                refresh(silent = true)
            }
        }
    }

    fun refresh(silent: Boolean = false) {
        if (!scope.isActive) return
        // El último refresco gana: cancela el anterior para que un poll lento
        // no pise una respuesta más nueva (pull-to-refresh/scan/otro tick).
        refreshJob?.cancel()
        refreshJob = scope.launch {
            if (!silent) _uiState.update { it.copy(isRefreshing = true) }
            runCatching { api.listMachines() }
                .onSuccess { machines ->
                    cache.save(machines)
                    _uiState.update {
                        it.copy(
                            isLoading = false,
                            machines = machines.distinctBy { m -> m.id },
                            isEmpty = machines.isEmpty(),
                            isOffline = false,
                            isRefreshing = false,
                        )
                    }
                }
                .onFailure { e ->
                    // 401 (token revocado/expirado) no es offline: es sesión inválida
                    // (story 1.6) → se emite el evento y se deja la lista tal cual.
                    if (e is ApiException && e.code == "unauthorized") {
                        _uiState.update { it.copy(isRefreshing = false) }
                        _sessionInvalid.tryEmit(Unit)
                    } else {
                        val cached = cache.get()
                        _uiState.update {
                            it.copy(
                                isLoading = false,
                                // Caché en memoria: muestra la última lista OK si existe (UX-DR4).
                                machines = if (cached.isNotEmpty()) cached else it.machines,
                                // El empty state ("No se encontraron máquinas") solo se muestra
                                // con 200 [] real; un fallo de red nunca lo finge (badge en su lugar).
                                isEmpty = false,
                                isOffline = true,
                                isRefreshing = false,
                            )
                        }
                    }
                }
        }
    }

    /** "Escanear ahora": POST /scan, spinner mientras escanea y refresco tras el 202 (AD-3). */
    fun scanNow() = withScopeOrNothing {
        if (_uiState.value.isScanning) return@withScopeOrNothing  // nunca dos POST en paralelo
        _uiState.update { it.copy(isScanning = true) }
        runCatching { api.triggerScan() }
            // 401 en /scan también es sesión inválida (el token viaja en cada petición).
            .onFailure {
                if (it is ApiException && it.code == "unauthorized") {
                    _sessionInvalid.tryEmit(Unit)
                } else {
                    _uiState.update { it.copy(isOffline = true) }
                }
            }
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
    // Nota: `refresh` y `scanNow` no usan withScopeOrNothing: lanzan jobs
    // nombrados con cancelación explícita (serialización de refreshes, 1.5).

    companion object {
        const val DEFAULT_POLL_INTERVAL_MS = 30_000L

        /**
         * Factory que cablea la red y la caché en memoria. La [WakemeupApi] la
         * construye `MainActivity` sobre la [com.wakemeup.manager.data.local.SettingsRepository]
         * segura (Keystore + cifrado, story 1.6); en tests se inyectan dobles.
         */
        fun createFactory(
            api: WakemeupApi,
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
