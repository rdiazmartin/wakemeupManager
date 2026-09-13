package com.wakemeup.manager.data.local

import com.wakemeup.manager.domain.Machine

/**
 * Caché en memoria de la última lista OK (UX-DR4): si un fetch falla,
 * la UI muestra la última lectura válida + badge "Sin conexión".
 */
class MachinesCache {
    @Volatile
    private var snapshot: List<Machine> = emptyList()

    fun save(machines: List<Machine>) {
        snapshot = machines
    }

    fun get(): List<Machine> = snapshot
}
