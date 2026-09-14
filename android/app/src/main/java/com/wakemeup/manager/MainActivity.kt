package com.wakemeup.manager

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.lifecycle.viewmodel.compose.viewModel
import com.wakemeup.manager.data.local.SettingsRepository
import com.wakemeup.manager.data.remote.WakemeupApi
import com.wakemeup.manager.ui.machines.MachineListScreen
import com.wakemeup.manager.ui.machines.MachineListViewModel
import com.wakemeup.manager.ui.setup.FirstRunScreen
import com.wakemeup.manager.ui.setup.SettingsScreen
import com.wakemeup.manager.ui.setup.SettingsViewModel
import com.wakemeup.manager.ui.theme.WakemeupTheme
import kotlinx.coroutines.flow.collect

/** Pantallas alcanzables de la app (story 1.6). */
private enum class StartupState { FIRST_RUN, SETTINGS, MACHINES }

/**
 * Actividad única: enruta entre primer arranque / ajustes / listado según la
 * configuración guardada y los eventos de sesión inválida (401 revocado →
 * limpia configuración y redirige a ajustes, FR-15 / UX-DR9). Story 1.6.
 * Navegación UDF sin librería (decisión 1.6): 3 estados, transiciones por
 * callbacks y eventos del ViewModel.
 */
class MainActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            WakemeupTheme {
                AppRoot(settings = settingsFactory(application), client = httpClientFactory())
            }
        }
    }

    companion object {
        /** Hook de tests: en producción siempre la impl segura (Keystore + cifrado). */
        @Volatile
        var settingsFactory: (android.app.Application) -> SettingsRepository = { ctx ->
            SettingsRepository.secure(ctx)
        }

        /** Hook de tests: cliente HTTP override (MockEngine en Robolectric). */
        @Volatile
        var httpClientFactory: () -> io.ktor.client.HttpClient = { WakemeupApi.defaultClient() }
    }
}

@Composable
internal fun AppRoot(settings: SettingsRepository, client: io.ktor.client.HttpClient) {
    // null = sesión aún cargándose (lectura instantánea del almacén cifrado).
    var startup by rememberSaveable { mutableStateOf<StartupState?>(null) }
    // Origen al abrir ajustes (FirstRun vs Listado) para que "volver" regrese
    // a la pantalla de la que se vino, no siempre al primer arranque.
    var settingsOrigin by rememberSaveable { mutableStateOf<StartupState>(StartupState.FIRST_RUN) }

    // Carga inicial de la configuración persistida (FR-15: la sesión se reutiliza);
    // resuelve el estado ANTES de componer listado/ajustes para que los ViewModels
    // nazcan con la URL/token reales (nada de fetches con URL vacía).
    LaunchedEffect(Unit) {
        val configured = settings.load()
        startup = if (configured) StartupState.MACHINES else StartupState.FIRST_RUN
    }

    when (startup ?: return) {
        StartupState.FIRST_RUN -> FirstRunScreen(
            onConfigure = {
                settingsOrigin = StartupState.FIRST_RUN
                startup = StartupState.SETTINGS
            },
        )

        StartupState.SETTINGS -> {
            val settingsViewModel: SettingsViewModel = viewModel(
                key = "settings-${settings.apiUrl}-${settings.deviceToken}",
                factory = SettingsViewModel.createFactory(settings = settings, api = rememberApi(settings, client)),
            )
            LaunchedEffect(Unit) {
                // Al entrar en la pantalla se limpia el flag previo de guardado:
                // re-guardar las MISMAS credenciales no debe auto-navegar.
                settingsViewModel.resetSaved()
            }
            SettingsScreen(
                onBack = { startup = settingsOrigin },
                onSaved = {
                    // save() ya actualizó la caché del repositorio; el cambio de
                    // apiUrl/deviceToken recrea api y ViewModels vía remember/key.
                    startup = StartupState.MACHINES
                },
                viewModel = settingsViewModel,
            )
        }

        StartupState.MACHINES -> {
            val machineViewModel: MachineListViewModel = viewModel(
                key = "machines-${settings.apiUrl}-${settings.deviceToken}",
                factory = MachineListViewModel.createFactory(api = rememberApi(settings, client)),
            )
            // 401 revocado en sesión activa → limpiar configuración y volver a
            // ajustes. Se re-colecta si el ViewModel se recrea (cambio de sesión).
            LaunchedEffect(machineViewModel) {
                machineViewModel.sessionInvalid.collect {
                    settings.clear()
                    machineViewModel.sessionInvalidHandled()
                    settingsOrigin = StartupState.FIRST_RUN
                    startup = StartupState.SETTINGS
                }
            }
            // Al salir del listado (ajustes u otro estado) se detiene el polling.
            DisposableEffect(machineViewModel) {
                onDispose { machineViewModel.stop() }
            }
            MachineListScreen(
                viewModel = machineViewModel,
                onOpenSettings = {
                    settingsOrigin = StartupState.MACHINES
                    startup = StartupState.SETTINGS
                },
            )
        }
    }
}

/** Api Ktor ligada a la sesión actual del repositorio (se recrea al guardar/limpiar). */
@Composable
private fun rememberApi(
    settings: SettingsRepository,
    client: io.ktor.client.HttpClient,
): WakemeupApi =
    remember(settings.apiUrl, settings.deviceToken) {
        WakemeupApi(settings, client)
    }
