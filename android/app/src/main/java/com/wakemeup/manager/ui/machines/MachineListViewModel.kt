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
import com.wakemeup.manager.data.remote.EventStream
import com.wakemeup.manager.data.remote.WakemeupApi
import com.wakemeup.manager.domain.EventOrigin
import com.wakemeup.manager.domain.Machine
import com.wakemeup.manager.domain.MachineEvent
import com.wakemeup.manager.notifications.MachineNotifier
import com.wakemeup.manager.notifications.NotificationPermissionPrompt
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
    /** Última acción de fila (alta/wake/shutdown): mensaje para snackbar. */
    val actionMessage: ActionMessage? = null,
)

/** Resultado de una acción de fila (epic 2, UX-DR7): snackbar de la pantalla. */
data class ActionMessage(
    val text: String,
    val isError: Boolean = false,
)

/** Estado de una acción en vuelo para la máquina (alta con progreso, UX-DR6). */
data class ActionState(
    val machineId: Int,
    val kind: ActionKind,
)

enum class ActionKind { ENROLL, WAKE, SHUTDOWN }

/** Diálogo abierto de la pantalla (UX-DR3/DR6): el alto vive en el ViewModel. */
sealed interface MachineDialog {
    /** Diálogo de alta: Usuario + Password con toggle y error inline (UX-DR6). */
    data class Enroll(val machine: Machine) : MachineDialog

    /** Diálogo de confirmación de apagado, SIEMPRE antes de apagar (UX-DR3). */
    data class Shutdown(val machine: Machine) : MachineDialog
}

class MachineListViewModel(
    private val api: WakemeupApi,
    private val cache: MachinesCache,
    /** Stream de eventos SSE (epic 3, AD-11); por defecto no escucha. */
    private val eventStream: EventStream = EventStream.noop(),
    /** Notificador del sistema para eventos de origen `mcp` (epic 3, FR-18). */
    private val notifier: MachineNotifier = MachineNotifier.noop(),
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
        eventJob?.cancel()
        eventJob = null
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

    /** Suscripción SSE activa (epic 3): se pausa con ahorro de batería. */
    private var eventJob: Job? = null

    /** Ahorro de batería: pausa el SSE (el polling de 30 s cubre el hueco). */
    private var batterySaver = false

    /**
     * true tras el primer fetch correcto: en el arranque en frío los cambios
     * `mcp` ya persistidos son históricos y solo se registran como baseline (sin
     * notificar); a partir de ahí, un cambio más nuevo sí notifica.
     */
    private var pollBaselineSeeded = false

    /**
     * Cambios ya notificados/recibidos por `mcp`, por id de máquina, con el
     * instante observado en ms epoch. Evita duplicar la notificación cuando el
     * mismo cambio llega por SSE y luego por el polling (y viceversa) y hace que
     * el polling solo notifique cambios NUEVOS (decisión del usuario, FR-18).
     * El SSM y la marca persistida del BE se generan por separado, así que la
     * comparación usa una tolerancia ([DEDUP_TOLERANCE_MS]).
     */
    private val seenMcpChanges = mutableMapOf<Int, Long>()

    /**
     * Prompts de permiso `POST_NOTIFICATIONS` que el notificador emite al primer
     * evento `mcp` (o al detectar que se denegó): la UI los consume para pedir
     * con explicación o sugerir ajustes. El colector debe estar suscrito antes
     * del primer evento `mcp` (el permiso se resuelve de forma oportunista).
     */
    val notificationPermissionPrompts: SharedFlow<NotificationPermissionPrompt> =
        notifier.permissionPrompts

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
        // El SSE se abre salvo en ahorro de batería (ahí manda el polling).
        if (!batterySaver) startEventStream()
    }

    /**
     * Abre la suscripción SSE (epic 3, AD-11): los eventos de cualquier origen
     * actualizan la fila sin spinner/snackbar; los `mcp` además notifican (FR-18).
     * Un 401 del stream sigue el camino de sesión inválida existente.
     */
    private fun startEventStream() {
        // `isActive` (no solo != null): un job terminado (401 o stream noop)
        // debe permitir reabrir el stream, p. ej. al desactivar ahorro de batería.
        if (eventJob?.isActive == true) return
        eventJob = scope.launch {
            try {
                eventStream.events().collect { event ->
                    applyEvent(event)
                }
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Throwable) {
                // Un 401 del stream es sesión inválida (token revocado), igual
                // que en el refresh; el resto de fallos los reintenta el cliente.
                if (e is ApiException && e.code == "unauthorized") {
                    _sessionInvalid.tryEmit(Unit)
                }
            }
        }
    }

    /**
     * Aplica un evento de estado a la lista (epic 3): actualiza la fila afectada
     * en `_uiState` sin spinner ni snackbar (acciones ajenas, UX-DR7).
     *
     * Notifica solo si el origen es `mcp`, la acción afecta a una máquina real
     * (no `scan_done`/tipos desconocidos), hay una fila que coincide y el mismo
     * cambio no fue ya notificado por el polling ([seenMcpChanges]).
     */
    fun applyEvent(event: MachineEvent) {
        val machineLabel = event.machine
        val status = event.statusChange
        var matchedId: Int? = null
        _uiState.update { state ->
            val index = state.machines.indexOfFirst {
                it.name == machineLabel || it.hostname == machineLabel || it.ip == machineLabel
            }
            if (index < 0) {
                state
            } else {
                val current = state.machines[index]
                matchedId = current.id
                val updated = current.copy(
                    status = status ?: current.status,
                    lastOrigin = event.origin,
                    lastChangeAt = event.timestamp,
                )
                state.copy(machines = state.machines.toMutableList().apply { this[index] = updated })
            }
        }
        if (event.origin != EventOrigin.MCP) return
        // `scan_done` (machine "-") y tipos desconocidos no son acciones de
        // máquina: nunca notifican (FR-18).
        if (!event.action.isMachineAction) return
        // Evento de una máquina que no está en la lista: no hay nada que notificar.
        val id = matchedId ?: return
        val millis = parseEpochMillis(event.timestamp)
        val seen = seenMcpChanges[id]
        // Si el polling ya notificó este mismo cambio (misma marca dentro de la
        // tolerancia), no se duplica (decisión del usuario, FR-18).
        if (millis != null && seen != null && millis <= seen + DEDUP_TOLERANCE_MS) return
        if (millis != null) seenMcpChanges[id] = millis
        notifier.notify(event)
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
                    val distinct = machines.distinctBy { m -> m.id }
                    cache.save(distinct)
                    // Fallback de polling (decisión del usuario): un cambio nuevo
                    // con `last_origin == mcp` que no llegó por SSE notifica en
                    // ≤30 s; si ya se vio (SSE o poll previo), no se duplica.
                    notifyPolledMcpChanges(distinct)
                    _uiState.update {
                        it.copy(
                            isLoading = false,
                            machines = distinct,
                            isEmpty = distinct.isEmpty(),
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

    /**
     * Fallback de notificación por polling.
     *
     * En el PRIMER fetch correcto tras arrancar se siembra el baseline con las
     * marcas `mcp` ya persistidas, SIN notificar: son cambios históricos (de
     * hace horas/días) y notificarlos en cada apertura de la app sería spam. A
     * partir de ahí solo notifica marcas estrictamente más nuevas (con la
     * tolerancia de dedup), de modo que la notificación corresponde a un cambio
     * vivo mientras la app está en marcha.
     */
    private fun notifyPolledMcpChanges(machines: List<Machine>) {
        if (!pollBaselineSeeded) {
            for (machine in machines) {
                if (machine.lastOrigin != EventOrigin.MCP) continue
                val millis = machine.lastChangeAt?.let(::parseEpochMillis) ?: continue
                // No rebajar una marca ya registrada por SSE (cambio vivo).
                val current = seenMcpChanges[machine.id]
                if (current == null || millis > current) seenMcpChanges[machine.id] = millis
            }
            pollBaselineSeeded = true
            return
        }
        for (machine in machines) {
            if (machine.lastOrigin != EventOrigin.MCP) continue
            val timestamp = machine.lastChangeAt ?: continue
            val millis = parseEpochMillis(timestamp) ?: continue
            val seen = seenMcpChanges[machine.id]
            // Ya visto por SSE o por un poll anterior (misma marca o anterior):
            // no se duplica (tolerancia por los dos relojes distintos).
            if (seen != null && millis <= seen + DEDUP_TOLERANCE_MS) continue
            seenMcpChanges[machine.id] = millis
            notifier.notify(
                MachineEvent(
                    type = "machine_change",
                    machine = machine.name,
                    timestamp = timestamp,
                    origin = EventOrigin.MCP,
                )
            )
        }
    }

    /** Instante del ISO-8601 UTC del BE (`...Z`/`+00:00`) en ms epoch, o null. */
    private fun parseEpochMillis(iso: String): Long? = try {
        java.time.Instant.parse(iso).toEpochMilli()
    } catch (_: Exception) {
        // Formatos sin offset (`2026-09-15T12:00:00`): se asume UTC.
        try {
            java.time.LocalDateTime.parse(iso).toInstant(java.time.ZoneOffset.UTC).toEpochMilli()
        } catch (_: Exception) {
            null
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
     * Ahorro de batería: el stream SSE se pausa y el polling de 30 s cubre el
     * hueco (FR-12/FR-18); al desactivarlo se reabre el SSE. El polling NO se
     * detiene (la spec de epic 3 mantiene el fallback activo).
     */
    fun setBatterySaver(enabled: Boolean) {
        batterySaver = enabled
        if (enabled) {
            eventJob?.cancel()
            eventJob = null
        } else if (pollJob != null) {
            startEventStream()
        }
    }

    // --- Acciones de fila (epic 2, UX-DR3/DR5/DR6/DR7) ---

    /** Acción de fila en vuelo: impide dobles taps y alimenta el progreso del diálogo. */
    private val _pendingAction = MutableStateFlow<ActionState?>(null)
    val pendingAction: StateFlow<ActionState?> = _pendingAction.asStateFlow()

    /** Mensaje de la última acción para consumir desde la pantalla (snackbar). */
    fun consumeActionMessage(): ActionMessage? {
        val msg = _uiState.value.actionMessage
        if (msg != null) _uiState.update { it.copy(actionMessage = null) }
        return msg
    }

    // --- Diálogos (UX-DR3/DR6) ---

    private val _dialog = MutableStateFlow<MachineDialog?>(null)
    val dialog: StateFlow<MachineDialog?> = _dialog.asStateFlow()

    /** Error inline del diálogo de alta (password a reintroducir, UX-DR6). */
    private val _enrollError = MutableStateFlow<String?>(null)
    val enrollError: StateFlow<String?> = _enrollError.asStateFlow()

    /** Abre el diálogo de alta de una máquina (desde la fila). */
    fun openEnrollDialog(machine: Machine) {
        _enrollError.value = null
        _dialog.value = MachineDialog.Enroll(machine)
    }

    /** Abre el diálogo de confirmación de apagado (nunca tap silencioso, UX-DR3). */
    fun openShutdownDialog(machine: Machine) {
        _dialog.value = MachineDialog.Shutdown(machine)
    }

    fun dismissDialog() {
        _dialog.value = null
        _enrollError.value = null
    }

    /** Alta de una máquina descubierta (UX-DR6): usuario + password de un solo uso. */
    fun enroll(machine: Machine, usuario: String, password: String) {
        if (_pendingAction.value != null) return
        _pendingAction.value = ActionState(machine.id, ActionKind.ENROLL)
        scope.launch {
            runCatching { api.enrollMachine(machine.id, usuario.trim(), password) }
                .onSuccess {
                    _dialog.value = null
                    _enrollError.value = null
                    postActionOk(MESSAGE_ALTA)
                }
                .onFailure { e -> postActionError(e, machine) }
        }
    }

    /** Encendido por WOL (UX-DR5): acción tonal de una fila offline gestionada. */
    fun wake(machine: Machine) {
        if (_pendingAction.value != null) return
        _pendingAction.value = ActionState(machine.id, ActionKind.WAKE)
        scope.launch {
            runCatching { api.wakeMachine(machine.id) }
                .onSuccess { postActionOk(MESSAGE_WAKE) }
                .onFailure { e -> postActionError(e, machine) }
        }
    }

    /** Apagado por SSH (UX-DR3): solo tras el diálogo de confirmación de la pantalla. */
    fun shutdown(machine: Machine) {
        if (_pendingAction.value != null) return
        _pendingAction.value = ActionState(machine.id, ActionKind.SHUTDOWN)
        scope.launch {
            runCatching { api.shutdownMachine(machine.id) }
                .onSuccess {
                    _dialog.value = null
                    postActionOk(MESSAGE_SHUTDOWN)
                }
                .onFailure { e ->
                    // 409 conflict (p. ej. fingerprint_mismatch → no_fiable):
                    // la acción falla del lado del BE, el diálogo se cierra y el
                    // error va al snackbar (UX-DR7: el reintento es a nivel de
                    // fila, no de diálogo).
                    if (e is ApiException && e.code == "conflict") {
                        _dialog.value = null
                    }
                    postActionError(e, machine)
                }
        }
    }

    private fun postActionOk(messageKey: String) {
        _pendingAction.value = null
        _uiState.update { it.copy(actionMessage = ActionMessage(text = messageKey)) }
        // Tras un control la lista se refresca (el estado real llega del BE, FR-2).
        refresh(silent = true)
    }

    private fun postActionError(e: Throwable, machine: Machine) {
        _pendingAction.value = null
        if (e is ApiException && e.code == "unauthorized") {
            // Regla (2.4): con el diálogo de alta abierto, un 401 de
            // enrollMachine es la password SSH rechazada (matriz 2.1); el 401
            // de sesión (token revocado) viene del refresh/otras acciones con
            // el diálogo cerrado: el contexto del diálogo ES el disambiguador.
            if (_dialog.value is MachineDialog.Enroll) {
                _enrollError.value = e.message ?: "action_error"
                return
            }
            _sessionInvalid.tryEmit(Unit)
            return
        }
        when (val d = _dialog.value) {
            // Alta: error inline (UX-DR6), el usuario re-introduce la password.
            is MachineDialog.Enroll -> _enrollError.value = e.message ?: "action_error"
            // Apagado: el diálogo sigue abierto sin snackbar (el 409 conflict
            // ya lo cerró en shutdown(); los demás errores reintentan en la
            // fila, UX-DR7).
            is MachineDialog.Shutdown -> Unit
            null -> _uiState.update {
                it.copy(actionMessage = ActionMessage(text = e.message ?: "action_error", isError = true))
            }
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
         * Tolerancia al comparar el timestamp del evento SSE con `last_change_at`
         * del polling: son dos marcas (y dos relojes) distintos del mismo cambio,
         * así que una marca dentro de esta ventana no se considera nueva.
         */
        const val DEDUP_TOLERANCE_MS = 2_000L

        /** Claves de mensaje de éxito de acción → resuelve strings ES/EN en la pantalla. */
        const val MESSAGE_ALTA = "action_message_alta_ok"
        const val MESSAGE_WAKE = "action_message_wake_ok"
        const val MESSAGE_SHUTDOWN = "action_message_shutdown_ok"

        /**
         * Factory que cablea la red y la caché en memoria. La [WakemeupApi] la
         * construye `MainActivity` sobre la [com.wakemeup.manager.data.local.SettingsRepository]
         * segura (Keystore + cifrado, story 1.6); en tests se inyectan dobles.
         */
        fun createFactory(
            api: WakemeupApi,
            cache: MachinesCache = MachinesCache(),
            eventStream: EventStream = EventStream.noop(),
            notifier: MachineNotifier = MachineNotifier.noop(),
        ): ViewModelProvider.Factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T = when {
                modelClass.isAssignableFrom(MachineListViewModel::class.java) ->
                    MachineListViewModel(
                        api = api,
                        cache = cache,
                        eventStream = eventStream,
                        notifier = notifier,
                    ) as T
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
