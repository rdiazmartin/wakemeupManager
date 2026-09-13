package com.wakemeup.manager.ui.machines

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Add
import androidx.compose.material.icons.outlined.PowerSettingsNew
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.wakemeup.manager.R
import com.wakemeup.manager.domain.Machine
import com.wakemeup.manager.domain.MachineStatus
import com.wakemeup.manager.ui.theme.InkSecondary
import com.wakemeup.manager.ui.theme.SuccessGreen
import com.wakemeup.manager.ui.theme.WarningAmber

/**
 * Fila de máquina conforme UX-DR2/DR5: punto 10dp + texto meta (nunca solo color),
 * nombre en título, IP/MAC monoespaciadas, acciones inline según estado.
 *
 * Las acciones se RENDERIZAN conforme al estado pero no tienen lógica en 1.5
 * (decisión de usuario: lógica en el Epic 2); TalkBack las anuncia como presentes
 * y el tap muestra un snackbar informativo vía [onActionTap].
 */
@Composable
fun MachineRow(
    machine: Machine,
    onActionTap: (Machine) -> Unit,
    modifier: Modifier = Modifier,
) {
    val offline = machine.status == MachineStatus.OFFLINE
    val noFiable = machine.status == MachineStatus.NO_FIABLE
    val online = machine.status == MachineStatus.ONLINE

    Surface(
        modifier = modifier.fillMaxWidth(),
        color = MaterialTheme.colorScheme.surface,
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 16.dp),
            horizontalArrangement = Arrangement.spacedBy(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            StatusDot(machine.status)
            Column(
                modifier = Modifier.weight(1f),
                verticalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Text(
                    text = machine.name,
                    style = MaterialTheme.typography.titleMedium,
                    color = if (offline) InkSecondary else MaterialTheme.colorScheme.onSurface,
                    maxLines = 1,
                )
                Row(
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = stringResource(statusTextRes(machine.status)),
                        style = MaterialTheme.typography.labelMedium,
                        color = if (noFiable) WarningAmber
                        else if (offline) InkSecondary
                        else MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        text = "${machine.ip}${machine.mac?.let { " · $it" } ?: ""}",
                        style = MaterialTheme.typography.labelSmall,
                        fontFamily = FontFamily.Monospace,
                        color = if (offline) InkSecondary.copy(alpha = 0.7f) else InkSecondary,
                    )
                }
            }
            Row(
                horizontalArrangement = Arrangement.spacedBy(8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                when {
                    // AD-10/UX-DR5: no_fiable → badge ámbar, sin acciones destructivas.
                    noFiable -> Unit
                    // Gestión de estado primero: offline → Encender; online → según gestión.
                    offline -> ActionButton(
                        label = R.string.machine_action_wake,
                        icon = Icons.Outlined.PowerSettingsNew,
                        tonal = true,
                        onTap = { onActionTap(machine) },
                    )
                    online && !machine.managed -> ActionButton(
                        label = R.string.machine_action_enroll,
                        icon = Icons.Outlined.Add,
                        tonal = true,
                        onTap = { onActionTap(machine) },
                    )
                    online -> ActionButton(
                        label = R.string.machine_action_shutdown,
                        icon = Icons.Outlined.PowerSettingsNew,
                        tonal = false,
                        onTap = { onActionTap(machine) },
                    )
                }
            }
        }
    }
}

@Composable
private fun ActionButton(
    label: Int,
    icon: ImageVector,
    tonal: Boolean,
    onTap: () -> Unit,
) {
    val text = @Composable {
        Icon(icon, contentDescription = null, modifier = Modifier.size(18.dp))
        Text(stringResource(label))
    }
    if (tonal) {
        FilledTonalButton(onClick = onTap) { text() }
    } else {
        OutlinedButton(onClick = onTap) { text() }
    }
}

@Composable
private fun StatusDot(status: MachineStatus) {
    val color = when (status) {
        MachineStatus.ONLINE -> SuccessGreen
        MachineStatus.OFFLINE -> InkSecondary
        MachineStatus.NO_FIABLE -> WarningAmber
    }
    Box(
        modifier = Modifier
            .size(10.dp)
            .background(color, CircleShape),
    )
}

private fun statusTextRes(status: MachineStatus): Int = when (status) {
    MachineStatus.ONLINE -> R.string.machine_status_online
    MachineStatus.OFFLINE -> R.string.machine_status_offline
    MachineStatus.NO_FIABLE -> R.string.machine_status_no_fiable
}
