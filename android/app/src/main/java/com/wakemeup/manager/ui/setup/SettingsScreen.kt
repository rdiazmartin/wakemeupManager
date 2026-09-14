package com.wakemeup.manager.ui.setup

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.Visibility
import androidx.compose.material.icons.outlined.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.wakemeup.manager.R

/**
 * Pantalla de ajustes (story 1.6): URL base + token de dispositivo, validación
 * local de formato, validación contra `GET /machines` (401 → token rechazado,
 * timeout/red → BE inalcanzable; UX-DR9), y guardado en el almacén seguro.
 * Se navega solo tras guardar con éxito ([SettingsViewModel.saved]).
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onBack: () -> Unit,
    onSaved: () -> Unit,
    viewModel: SettingsViewModel,
) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val saved by viewModel.saved.collectAsStateWithLifecycle()

    LaunchedEffect(saved) {
        if (saved) onSaved()
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.surface,
        topBar = {
            TopAppBar(
                title = { Text(stringResource(R.string.settings_title)) },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            Icons.AutoMirrored.Outlined.ArrowBack,
                            contentDescription = stringResource(R.string.settings_back),
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.surface,
                ),
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState())
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Text(
                text = stringResource(R.string.settings_intro),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            OutlinedTextField(
                value = state.apiUrlBase,
                onValueChange = viewModel::onUrlChanged,
                label = { Text(stringResource(R.string.settings_url_label)) },
                placeholder = { Text(stringResource(R.string.settings_url_hint)) },
                singleLine = true,
                isError = state.formatError,
                supportingText = {
                    Text(
                        text = stringResource(
                            if (state.formatError) R.string.settings_error_format
                            else R.string.settings_url_helper
                        )
                    )
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .testTag("settings_url_field"),
            )

            TokenField(
                value = state.deviceToken,
                onValueChange = viewModel::onTokenChanged,
            )

            if (state.tokenRejected) {
                ErrorText(stringResource(R.string.settings_error_token_rejected))
            }
            if (state.backendUnreachable) {
                ErrorText(stringResource(R.string.settings_error_unreachable))
            }

            Button(
                onClick = viewModel::save,
                enabled = !state.isSaving,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(48.dp)
                    .testTag("settings_save_button"),
            ) {
                Text(
                    text = if (state.isSaving) stringResource(R.string.settings_saving)
                    else stringResource(R.string.settings_save),
                )
            }
        }
    }
}

@Composable
private fun ErrorText(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.bodyMedium,
        color = MaterialTheme.colorScheme.error,
    )
}

/** Campo de token con visibilidad alternable (no queda en texto plano en pantalla). */
@Composable
private fun TokenField(
    value: String,
    onValueChange: (String) -> Unit,
) {
    var visible by remember { mutableStateOf(false) }
    OutlinedTextField(
        value = value,
        onValueChange = onValueChange,
        label = { Text(stringResource(R.string.settings_token_label)) },
        singleLine = true,
        visualTransformation =
        if (visible) VisualTransformation.None else PasswordVisualTransformation(),
        trailingIcon = {
            Row {
                IconButton(onClick = { visible = !visible }) {
                    Icon(
                        imageVector = if (visible) Icons.Outlined.VisibilityOff else Icons.Outlined.Visibility,
                        contentDescription = stringResource(
                            if (visible) R.string.settings_token_hide else R.string.settings_token_show
                        ),
                    )
                }
            }
        },
        modifier = Modifier
            .fillMaxWidth()
            .testTag("settings_token_field"),
    )
}
