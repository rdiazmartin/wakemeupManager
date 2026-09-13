package com.wakemeup.manager.ui.machines

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
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Refresh
import androidx.compose.material.icons.outlined.Search
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.wakemeup.manager.R
import com.wakemeup.manager.domain.Machine
import com.wakemeup.manager.ui.theme.AccentMint
import com.wakemeup.manager.ui.theme.GraphiteBase
import com.wakemeup.manager.ui.theme.GraphiteRaised
import com.wakemeup.manager.ui.theme.InkPrimary
import com.wakemeup.manager.ui.theme.InkSecondary
import com.wakemeup.manager.ui.theme.WarningAmber
import kotlinx.coroutines.launch

/**
 * Pantalla de listado de máquinas (FR-12 + UX-DR4): polling 30 s pausable en
 * ahorro de batería, pull-to-refresh, "escanear ahora" en el header con spinner
 * (texto "Escaneando…" con Reduce Motion), skeleton 4-6 filas en el primer fetch,
 * lista vacía con acción de escaneo, caché + badge "Sin conexión" sin romper contenido.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MachineListScreen(
    viewModel: MachineListViewModel = viewModel(
        factory = MachineListViewModel.createFactory(),
    ),
    modifier: Modifier = Modifier,
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val snackbarHostState = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current

    LaunchedEffect(Unit) {
        viewModel.start()
        viewModel.setBatterySaver(MachineListViewModel.isBatterySaverActive(context))
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
                        onClick = viewModel::scanNow,
                    )
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
                uiState.isEmpty -> EmptyState(onScan = viewModel::scanNow)
                else -> MachineList(
                    machines = uiState.machines,
                    onRefresh = { viewModel.refresh() },
                    onActionTap = {
                        scope.launch {
                            snackbarHostState.showSnackbar(
                                message = context.getString(R.string.machine_action_noop_info),
                            )
                        }
                    },
                )
            }
        }
    }
}

@Composable
private fun ScanNowButton(scanning: Boolean, onClick: () -> Unit) {
    val reduceMotion = isReduceMotionEnabled()
    if (scanning) {
        Row(
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 16.dp),
        ) {
            if (!reduceMotion) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    strokeWidth = 2.dp,
                    color = AccentMint,
                )
            }
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
    onRefresh: () -> Unit,
    onActionTap: (Machine) -> Unit,
) {
    var refreshing by remember { mutableStateOf(false) }
    LaunchedEffect(refreshing) {
        if (refreshing) {
            onRefresh()
            refreshing = false
        }
    }
    PullToRefreshBox(
        isRefreshing = refreshing,
        onRefresh = { refreshing = true },
        modifier = Modifier.fillMaxSize(),
    ) {
        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            contentPadding = PaddingValues(vertical = 8.dp),
        ) {
            items(machines, key = { it.id }) { machine ->
                MachineRow(
                    machine = machine,
                    onActionTap = onActionTap,
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
private fun EmptyState(onScan: () -> Unit) {
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
            TextButton(onClick = onScan) {
                Icon(Icons.Outlined.Search, contentDescription = null)
                Text(stringResource(R.string.machines_scan_now))
            }
        }
    }
}
