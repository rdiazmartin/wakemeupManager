package com.wakemeup.manager

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.wakemeup.manager.ui.machines.MachineListScreen
import com.wakemeup.manager.ui.theme.WakemeupTheme

/**
 * Actividad única de la app en 1.5: pantalla de listado de máquinas.
 * (El enrutado a ajustes/primer arranque llega en la story 1.6.)
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            WakemeupTheme {
                MachineListScreen()
            }
        }
    }
}
