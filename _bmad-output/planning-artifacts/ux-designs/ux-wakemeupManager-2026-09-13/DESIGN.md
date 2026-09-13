---
name: wakemeupManager
description: Control de encendido/apagado de máquinas de la red doméstica, vía WOL + SSH desde una Raspberry. Oscuro primero, Material You dinámico, acciones inline.
status: draft
colors:
  surface-base: '#121417'
  surface-raised: '#1A1D21'
  surface-sunken: '#0D0F11'
  ink-primary: '#E6E8EB'
  ink-secondary: '#9BA4AE'
  ink-disabled: '#5C646D'
  accent: '#2DE0A5'
  accent-contrast: '#0B3B2A'
  success: '#4CC38A'
  warning: '#F2B24C'
  danger: '#F26D6D'
typography:
  title:
    note: 'Material 3 — Headline Small; lista principal usa Title Large para nombres de máquina'
  body:
    note: 'Material 3 — Body Large'
  meta:
    note: 'Material 3 — Body Small / Label Medium'
rounded:
  sm: 8px
  md: 16px
spacing:
  '1': 4dp
  '2': 8dp
  '3': 12dp
  '4': 16dp
  '5': 24dp
  '6': 32dp
---

## Brand & Style

wakemeupManager es el interruptor remoto de la red de casa: una herramienta técnica pero amable, para personas que no saben qué es un magic packet ni un host key. La estética es de panel de control en penumbra: superficies grafito profundas, un único acento eléctrico que significa "energía/acción", y estados de color que se leen de un vistazo (verde = encendida, gris = apagada, ámbar = aviso, rojo = error). Nada de brillo, nada de ruido: la lista y sus botones son los protagonistas.

Es una app pública y gratuita: se distribuye, pero cada instalación habla con el BE de quien la usa. El primer arranque debe sentirse como encender un equipo nuevo: oscuro, nítido, sin sorpresas.

## Colors

La paleta base es un tema oscuro propio (usado como fallback y en pantallas previas a la primera configuración). Con Material You activo, los tonos de superficie y acento los dicta el sistema (`colorScheme` dinámico), conservando siempre los roles semánticos de estado:

- **Superficies (`surface-base`/`raised`/`sunken`)** — grafito profundo; la base es el lienzo, `raised` eleva tarjetas y modales, `sunken` acoge campos y zonas de entrada.
- **Ink (`ink-primary`/`secondary`/`disabled`)** — texto e iconografía en grises cálidos; nunca negro puro ni blanco puro.
- **Acento eléctrico (`accent`)** — verde menta teñido de neón en fallback. Solo para acciones primarias (Encender) y elementos interactivos clave. Con Material You, lo sustituye el color dinámico del tema.
- **Semánticos de estado** — `success` (online), `warning` (no fiable / WOL en WiFi / sin sudo), `danger` (offline con problema, acciones destructivas, errores). Se usan en indicadores y textículos de estado, nunca como fill de superficies grandes.
- **Do**: estados solo en la fila como punto + texto corto.
- **Don't**: gradientes, acentos saturados en toda la interfaz, brillos de neón decorativos, fondo rojo/alerta en toda la pantalla.

## Typography

Material 3 por defecto. `title` para nombres de máquina y cabecera de configuración; `body` para texto de contexto y microcopy; `meta` para IP/MAC y estados secundarios. Respeto de Dynamic Type del sistema en todos los niveles (la mayor escala debe renderizar sin truncar controles). Nada de itálicas para datos técnicos; la IP/MAC se muestra en `ui-monospace` (familia system monospace) para que la lectura técnica sea nítida.

## Layout & Spacing

Escala 4/8/12/16/24/32. Margen lateral 16dp, separación vertical entre filas 16dp. La lista es el lienzo: filas con altura ≥72dp para acomodar botones táctiles, con hairline sutil entre filas. Los modales (alta, apagado) son de un solo nivel: nunca dos modales apilados. Botones de acción siempre alineados a la derecha de la fila, etiquetados con icono + texto.

## Elevation & Depth

Elevación mínima: la jerarquía viene de tono (raised sobre base) y no de sombras. Sombras reservadas al overlay de modales y snackbars. Sin sombras decorativas en filas.

## Shapes

`rounded/sm` (8px) para filas de lista y botones; `rounded/md` (16px) para modales, tarjetas de configuración y bottomsheets. Iconos con las formas de Material Icons (relleno en estados activos, outline en inactivos).

## Components

- **Machine row** — `surface-base`, sin card, hairline de separación. Contenido: indicador de estado (punto 10dp + texto `meta`), nombre de máquina (`title`), IP/MAC (`meta` monoespaciada). Acciones a la derecha: `Encender` (botón tonal con icono `power`) y/o `Apagar` (botón con icono + borde). Fila offline: indicador gris y botones con menos contraste; fila no fiable (fingerprint mismatch): badge ámbar `No fiable` en `meta`.
- **Power off dialog** — modal `md` centrado, icono de advertencia en `danger`, título "¿Apagar <máquina>?", cuerpo técnico, acciones `Cancelar` / `Apagar` (botón de texto peligro). Nunca se omite: el apagado en remoto desconecta la máquina sin que el usuario esté delante.
- **Enroll sheet / dialog** — modal con campos `Usuario` y `Password` (oculta con toggle de visibilidad), aviso explícito: "La password se usa una sola vez y no se guarda." CTA: `Dar de alta`.
- **Header actions** — barra superior: título `wakemeupManager`, iconos `escaneo` (refresco forzado, gira mientras escanea) y `engine` (ajustes).
- **Snackbar** — resultados de acciones: éxito breve ("Encendido enviado", "Máquina apagada"), errores con motivo ("Máquina no responde", "Token rechazado — revisa ajustes").

## Do's and Don'ts

| Do | Don't |
|---|---|
| Un acento, acciones primarias | Colorear cada máquina con su propio acento |
| Estados con punto + texto corto | Estado solo por color (inaccesible) |
| Advertencia en todo apagado | Apagar con un tap silencioso |
| Microcopy que explica sin tecnicismos | Jerga como "magic packet", "broadcast", "ssh-copy-id" |
| Dark-first, sigue el tema del sistema | Tema claro por defecto |
| Botones con icono + texto | Iconos sueltos ambiguos |
