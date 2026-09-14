package com.wakemeup.manager.ui.machines

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material.icons.outlined.Settings
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material.icons.outlined.VisibilityOff
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.wakemeup.manager.R
import com.wakemeup.manager.data.local.SettingsRepository
import com.wakemeup.manager.data.remote.WakemeupApi
import com.wakemeup.manager.domain.Machine
import com.wakemeup.manager.ui.theme.AccentMint
import com.wakemeup.manager.ui.theme.GraphiteBase
import com.wakemeup.manager.ui.theme.GraphiteRaised
import com.wakemeup.manager.ui.theme.InkPrimary
import com.wakemeup.manager.ui.theme.InkSecondary
import com.wakemeup.manager.ui.theme.WarningAmber

/**
 * Pantalla de listado de máquinas (FR-12 + UX-DR4): polling 30 s pausable en
 * ahorro de batería, pull-to-refresh, "escanear ahora" en el header con spinner
 * (texto "Escaneando…" con Reduce Motion), skeleton 4-6 filas en el primer fetch,
 * lista vacía con acción de escaneo, caché + badge "Sin conexión" sin romper contenido.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MachineListScreen(
    viewModel: MachineListViewModel? = null,
    modifier: Modifier = Modifier,
    onOpenSettings: () -> Unit = {},
) {
    // Si no se inyecta (MainActivity), la pantalla crea su ViewModel con la
    // configuración segura del dispositivo (Keystore + cifrado, story 1.6).
    // El contexto se captura ANTES del remember (LocalContext es @Composable);
    // la rama de fallback solo se compone con viewModel null.
    val resolvedViewModel: MachineListViewModel = if (viewModel == null) {
        val fallbackContext = LocalContext.current.applicationContext
        val fallbackApi = remember {
            WakemeupApi(SettingsRepository.secure(fallbackContext))
        }
        viewModel(factory = MachineListViewModel.createFactory(api = fallbackApi))
    } else {
        viewModel
    }
    val uiState by resolvedViewModel.uiState.collectAsStateWithLifecycle()
    val dialog by resolvedViewModel.dialog.collectAsStateWithLifecycle()
    val pendingAction by resolvedViewModel.pendingAction.collectAsStateWithLifecycle()
    val enrollError by resolvedViewModel.enrollError.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    val context = LocalContext.current

    // Snackbar de resultados de acción (UX-DR7): consume los mensajes SECUENCIALMENTE
    // (un solo collector que espera a que showSnackbar termine antes de sacar el
    // siguiente) — un mensaje que llega mientras otro se muestra NO cancela el
    // visible (LaunchedEffect(key) con key variable lo cancelaría a mitad).
    fun resolveActionText(text: String): String = when (text) {
        MachineListViewModel.MESSAGE_ALTA -> context.getString(R.string.action_message_alta_ok)
        MachineListViewModel.MESSAGE_WAKE -> context.getString(R.string.action_message_wake_ok)
        MachineListViewModel.MESSAGE_SHUTDOWN -> context.getString(R.string.action_message_shutdown_ok)
        else -> text
    }
    val messageEvents = remember { kotlinx.coroutines.flow.MutableSharedFlow<ActionMessage>(extraBufferCapacity = 16) }
    LaunchedEffect(resolvedViewModel) {
        // Sondeo barato del cola de mensajes del ViewModel (consumo único).
        while (true) {
            resolvedViewModel.consumeActionMessage()?.let { messageEvents.tryEmit(it) }
            kotlinx.coroutines.delay(250)
        }
    }
    LaunchedEffect(resolvedViewModel, messageEvents) {
        messageEvents.collect { msg ->
            // showSnackbar suspende hasta que el actual se oculta: el siguiente
            // mensaje espera sin cancelar el visible (cola FIFO).
            snackbarHostState.showSnackbar(resolveActionText(msg.text))
        }
    }

    // Ahorro de batería reactivo: también se re-evalúa cuando el sistema lo
    // activa/desactiva en caliente (ACTION_POWER_SAVE_MODE_CHANGED).
    val batterySaver by produceState(
        initialValue = MachineListViewModel.isBatterySaverActive(context) ||
            MachineListViewModel.isBatteryLow(context),
        context,
    ) {
        value = MachineListViewModel.isBatterySaverActive(context) ||
            MachineListViewModel.isBatteryLow(context)
        val filter = IntentFilter().apply {
            addAction(PowerManager.ACTION_POWER_SAVE_MODE_CHANGED)
            addAction(Intent.ACTION_BATTERY_LOW)
        }
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(c: Context?, intent: Intent?) {
                value = MachineListViewModel.isBatterySaverActive(context) ||
                    MachineListViewModel.isBatteryLow(context)
            }
        }
        context.registerReceiver(receiver, filter)
        awaitDispose { context.unregisterReceiver(receiver) }
    }
    LaunchedEffect(batterySaver) {
        resolvedViewModel.setBatterySaver(batterySaver)
    }

    LaunchedEffect(Unit) {
        resolvedViewModel.start()
    }

    // Diálogos (UX-DR3/DR6): siempre sobre el Scaffold; el de apagado se
    // anuncia como diálogo (a11y). `inFlight = pendingAction != null` (CUALQUIER
    // máquina): mientras una acción vuela, los botones de confirmar quedan
    // deshabilitados (un tap no se descarta en silencio).
    when (val d = dialog) {
        is MachineDialog.Enroll -> EnrollDialog(
            machine = d.machine,
            inFlight = pendingAction != null,
            error = enrollError,
            onConfirm = { usuario, password ->
                resolvedViewModel.enroll(d.machine, usuario, password)
            },
            onDismiss = resolvedViewModel::dismissDialog,
        )
        is MachineDialog.Shutdown -> ShutdownDialog(
            machine = d.machine,
            inFlight = pendingAction != null,
            onConfirm = { resolvedViewModel.shutdown(d.machine) },
            onDismiss = resolvedViewModel::dismissDialog,
        )
        null -> Unit
    }

    Scaffold(
        modifier = modifier.fillMaxSize(),
        snackbarHost = { SnackbarHost(snackbarHostState) },
        containerColor = GraphiteBase,
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = stringResource(R.string.machines_title),
                        style = MaterialTheme.typography.titleMedium,
                        color = InkPrimary,
                    )
                },
                actions = {
                    ScanNowButton(
                        scanning = uiState.isScanning,
                        reduceMotion = isReduceMotionEnabled(),
                        onClick = resolvedViewModel::scanNow,
                    )
                    IconButton(
                        onClick = onOpenSettings,
                        modifier = Modifier
                            .padding(horizontal = 8.dp)
                            .size(48.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Outlined.Settings,
                            contentDescription = stringResource(R.string.machines_open_settings),
                            tint = InkSecondary,
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = GraphiteBase,
                    scrolledContainerColor = GraphiteBase,
                ),
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            OfflineBanner(visible = uiState.isOffline)
            when {
                uiState.isLoading -> SkeletonList()
                uiState.isEmpty -> EmptyState(
                    onScan = resolvedViewModel::scanNow,
                    scanning = uiState.isScanning,
                )
                else -> MachineList(
                    machines = uiState.machines,
                    isRefreshing = uiState.isRefreshing,
                    onRefresh = { resolvedViewModel.refresh() },
                    onEnroll = resolvedViewModel::openEnrollDialog,
                    onWake = resolvedViewModel::wake,
                    onShutdown = resolvedViewModel::openShutdownDialog,
                )
            }
        }
    }
}

@Composable
internal fun ScanNowButton(
    scanning: Boolean,
    reduceMotion: Boolean,
    onClick: () -> Unit,
) {
    if (scanning) {
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 16.dp),
        ) {
            if (!reduceMotion) {
                CircularProgressIndicator(
                    modifier = Modifier
                        .size(18.dp)
                        .testTag("scan_spinner"),
                    strokeWidth = 2.dp,
                    color = AccentMint,
                )
            }
            // Reduce Motion: el spinner se sustituye por texto ("Escaneando…").
            Text(
                text = stringResource(R.string.machines_scanning),
                style = MaterialTheme.typography.labelMedium,
                color = InkSecondary,
            )
        }
    } else {
        IconButton(
            onClick = onClick,
            modifier = Modifier
                .padding(horizontal = 8.dp)
                .size(48.dp),
        ) {
            Icon(
                imageVector = Icons.Outlined.Refresh,
                contentDescription = stringResource(R.string.machines_scan_now),
                tint = AccentMint,
            )
        }
    }
}

/** Heurística de Reduce Motion: escala de duración de animaciones del sistema en 0. */
@Composable
private fun isReduceMotionEnabled(): Boolean {
    val context = LocalContext.current
    return remember(context) {
        try {
            Settings.Global.getInt(
                context.contentResolver,
                Settings.Global.ANIMATOR_DURATION_SCALE,
                1,
            ) == 0
        } catch (_: Exception) {
            false
        }
    }
}

@Composable
private fun OfflineBanner(visible: Boolean) {
    if (!visible) return
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .background(GraphiteRaised)
            .padding(horizontal = 16.dp, vertical = 8.dp),
        contentAlignment = Alignment.CenterStart,
    ) {
        Text(
            text = stringResource(R.string.machines_offline_badge),
            style = MaterialTheme.typography.labelMedium,
            color = WarningAmber,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MachineList(
    machines: List<Machine>,
    isRefreshing: Boolean,
    onRefresh: () -> Unit,
    onEnroll: (Machine) -> Unit,
    onWake: (Machine) -> Unit,
    onShutdown: (Machine) -> Unit,
) {
    PullToRefreshBox(
        isRefreshing = isRefreshing,
        onRefresh = onRefresh,
        modifier = Modifier.fillMaxSize(),
    ) {
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(vertical = 8.dp),
        ) {
            items(machines, key = { it.id }) { machine ->
                MachineRow(
                    machine = machine,
                    onEnroll = onEnroll,
                    onWake = onWake,
                    onShutdown = onShutdown,
                )
                HorizontalDivider(
                    modifier = Modifier.padding(horizontal = 16.dp),
                    color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.4f),
                )
            }
        }
    }
}

/** Skeleton de 4-6 filas durante el primer fetch (UX-DR4). */
@Composable
private fun SkeletonList() {
    Column(modifier = Modifier.fillMaxSize()) {
        repeat(5) {
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 16.dp)
                    .height(64.dp)
                    .background(GraphiteRaised),
            )
        }
    }
}

/** Lista vacía con mensaje y acción de escaneo (UX-DR4). */
@Composable
private fun EmptyState(onScan: () -> Unit, scanning: Boolean) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = stringResource(R.string.machines_empty),
                style = MaterialTheme.typography.titleMedium,
                color = InkPrimary,
                modifier = Modifier.semantics { heading() },
            )
            Text(
                text = stringResource(R.string.machines_empty_scan_first),
                style = MaterialTheme.typography.bodyMedium,
                color = InkSecondary,
            )
            TextButton(onClick = onScan, enabled = !scanning) {
                Icon(Icons.Outlined.Search, contentDescription = null)
                Text(stringResource(R.string.machines_scan_now))
            }
        }
    }
}

/**
 * Diálogo de confirmación de apagado (UX-DR3): SIEMPRE antes de ejecutar el
 * shutdown; anunciado como diálogo para TalkBack. "Apagar" con loader mientras
 * la acción vuela; Cancelar cierra sin ejecutar.
 */
@Composable
private fun ShutdownDialog(
    machine: Machine,
    inFlight: Boolean,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = { if (!inFlight) onDismiss() },
        title = { Text(stringResource(R.string.shutdown_dialog_title)) },
        text = {
            Text(
                stringResource(
                    R.string.shutdown_dialog_body,
                    machine.name,
                )
            )
        },
        confirmButton = {
            TextButton(
                onClick = onConfirm,
                enabled = !inFlight,
                modifier = Modifier.testTag("shutdown_dialog_confirm"),
            ) {
                if (inFlight) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        strokeWidth = 2.dp,
                    )
                }
                Text(stringResource(R.string.shutdown_dialog_confirm))
            }
        },
        dismissButton = {
            TextButton(
                onClick = onDismiss,
                enabled = !inFlight,
                modifier = Modifier.testTag("shutdown_dialog_cancel"),
            ) {
                Text(stringResource(R.string.shutdown_dialog_cancel))
            }
        },
    )
}

/**
 * Diálogo de alta con password de un solo uso (UX-DR6): campos Usuario +
 * Password (con toggle de visibilidad), aviso "La password se usa una sola vez
 * y no se guarda", CTA "Dar de alta" con progreso mientras vuela y error inline
 * (la password se reintroduce). La password no se persiste en NINGÚN almacén.
 */
@Composable
private fun EnrollDialog(
    machine: Machine,
    inFlight: Boolean,
    error: String?,
    onConfirm: (usuario: String, password: String) -> Unit,
    onDismiss: () -> Unit,
) {
    var usuario by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var passwordVisible by remember { mutableStateOf(false) }

    AlertDialog(
        onDismissRequest = { if (!inFlight) onDismiss() },
        title = { Text(stringResource(R.string.enroll_dialog_title, machine.name)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    value = usuario,
                    onValueChange = { usuario = it },
                    label = { Text(stringResource(R.string.enroll_dialog_user_label)) },
                    singleLine = true,
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag("enroll_user_field"),
                )
                OutlinedTextField(
                    value = password,
                    onValueChange = { password = it },
                    label = { Text(stringResource(R.string.enroll_dialog_password_label)) },
                    singleLine = true,
                    visualTransformation = if (passwordVisible) {
                        VisualTransformation.None
                    } else {
                        PasswordVisualTransformation()
                    },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    trailingIcon = {
                        IconButton(onClick = { passwordVisible = !passwordVisible }) {
                            Icon(
                                imageVector = if (passwordVisible) {
                                    Icons.Outlined.VisibilityOff
                                } else {
                                    Icons.Outlined.Visibility
                                },
                                contentDescription = stringResource(
                                    if (passwordVisible) {
                                        R.string.enroll_dialog_hide_password
                                    } else {
                                        R.string.enroll_dialog_show_password
                                    }
                                ),
                            )
                        }
                    },
                    modifier = Modifier
                        .fillMaxWidth()
                        .testTag("enroll_password_field"),
                )
                Text(
                    text = stringResource(R.string.enroll_dialog_once_hint),
                    style = MaterialTheme.typography.bodySmall,
                    color = InkSecondary,
                )
                if (error != null) {
                    Text(
                        text = error,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.testTag("enroll_error"),
                    )
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = {
                    if (usuario.isNotBlank() && password.isNotBlank()) {
                        onConfirm(usuario, password)
                    }
                },
                enabled = !inFlight && usuario.isNotBlank() && password.isNotBlank(),
                modifier = Modifier.testTag("enroll_dialog_confirm"),
            ) {
                if (inFlight) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        strokeWidth = 2.dp,
                    )
                }
                Text(stringResource(R.string.enroll_dialog_confirm))
            }
        },
        dismissButton = {
            TextButton(
                onClick = onDismiss,
                enabled = !inFlight,
                modifier = Modifier.testTag("enroll_dialog_cancel"),
            ) {
                Text(stringResource(R.string.enroll_dialog_cancel))
            }
        },
    )
}
