package com.wakemeup.manager.ui.machines

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.google.common.truth.Truth.assertThat
import com.wakemeup.manager.domain.Machine
import com.wakemeup.manager.domain.MachineStatus
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * Cobertura Robolectric de la fila de máquina (requisito transversal):
 * punto + texto meta, nombre, IP/MAC monoespaciada y acciones por estado
 * (UX-DR2/DR5: descubierta → Dar de alta; offline → Encender; online gestionada →
 * Apagar; no_fiable → badge ámbar y sin acciones destructivas).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "es")
class MachineRowTest {

    @get:Rule
    val compose = createComposeRule()

    private fun machine(
        status: MachineStatus,
        managed: Boolean = false,
        mac: String? = "AA:BB:CC:DD:EE:FF",
    ) = Machine(
        id = 1,
        name = "Desktop",
        ip = "192.168.1.10",
        mac = mac,
        hostname = "desktop",
        status = status,
        managed = managed,
    )

    @Test
    fun `fila descubierta online muestra nombre punto ip mac y accion dar de alta`() {
        compose.setContent {
            MachineRow(machine = machine(MachineStatus.ONLINE, managed = false), onActionTap = {})
        }
        compose.onNodeWithText("Desktop").assertIsDisplayed()
        compose.onNodeWithText("192.168.1.10 · AA:BB:CC:DD:EE:FF").assertIsDisplayed()
        compose.onNodeWithText("Encendida").assertIsDisplayed()
        compose.onNodeWithText("Dar de alta").assertIsDisplayed()
    }

    @Test
    fun `fila offline muestra estado off y accion encender`() {
        compose.setContent {
            MachineRow(machine = machine(MachineStatus.OFFLINE), onActionTap = {})
        }
        compose.onNodeWithText("Desktop").assertIsDisplayed()
        compose.onNodeWithText("Apagada").assertIsDisplayed()
        compose.onNodeWithText("Encender").assertIsDisplayed()
    }

    @Test
    fun `fila gestionada online muestra accion apagar con borde`() {
        compose.setContent {
            MachineRow(machine = machine(MachineStatus.ONLINE, managed = true), onActionTap = {})
        }
        compose.onNodeWithText("Encendida").assertIsDisplayed()
        compose.onNodeWithText("Apagar").assertIsDisplayed()
    }

    @Test
    fun `fila no fiable muestra badge y ninguna accion destructiva`() {
        compose.setContent {
            MachineRow(machine = machine(MachineStatus.NO_FIABLE, managed = true), onActionTap = {})
        }
        compose.onNodeWithText("No fiable").assertIsDisplayed()
        compose.onNodeWithText("Apagar").assertDoesNotExist()
        compose.onNodeWithText("Encender").assertDoesNotExist()
        compose.onNodeWithText("Dar de alta").assertDoesNotExist()
    }

    @Test
    fun `fila sin mac omite el separador y solo muestra la ip`() {
        compose.setContent {
            MachineRow(machine = machine(MachineStatus.OFFLINE, mac = null), onActionTap = {})
        }
        compose.onNodeWithText("192.168.1.10").assertIsDisplayed()
    }

    @Test
    fun `tap en accion notifica al padre (acciones renderizadas, logica en epic 2)`() {
        var tapped: Machine? = null
        compose.setContent {
            MachineRow(machine = machine(MachineStatus.OFFLINE), onActionTap = { tapped = it })
        }
        compose.onNodeWithText("Encender").performClick()
        assertThat(tapped).isNotNull()
        assertThat(tapped?.name).isEqualTo("Desktop")
    }
}
