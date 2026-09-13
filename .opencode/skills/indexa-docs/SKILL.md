---
name: indexa-docs
description: Use when the documentation index of a repo needs to be generated, refreshed, or verified, including BMAD-generated docs (specs, ADR, sprint-status). Runs a script that writes a single INDICE.md (one entry per doc with a semantic, decision-ready description) and reviews the changes. Install in repos alongside sesion-bitacora. Trigger also on doc creation, rename, move, or deletion.
---

# indexa-docs — índice de documentación semántico para el agente

Genera y mantiene **`INDICE.md`** en la raíz del repo: un único maestro que el agente
puede **entender de un vistazo**. El objetivo NO es ahorrar tokens, sino **ganancia
semántica**: cada entrada debe decirle al agente *qué contiene* el doc y *cuándo debe
abrirlo*, sin necesidad de leer el fichero para saber que existe.

## Criterio de diseño (prioridad: comprensión semántica)

1. **Una entrada por doc**, con descripción que responde a:
   - **Qué es / qué contiene** (tema, alcance).
   - **Cuándo consultarla** (para qué tarea, qué decisión resuelve).
   - Se permiten **1-2 líneas** cuando aporte; NO truncar por frugalidad de tokens.
2. **Un solo maestro** (`INDICE.md` en la raíz): el agente lee UN fichero y tiene el
   mapa completo. Evita saltos entre mini-índices.
3. **Frontmatter** que resume el conjunto: `titulo`, `categorias`, `entradas`,
   `actualizado`.
4. **Doc nativa** (raíz, `docs/`, `sesiones/`, subcarpetas con `*.md`) lista
   **archivo a archivo** con descripción rica.
5. **Doc BMAD** (`_bmad-output/`, `.bmad/`) se indexa por **carpetas clave** y un
   puñado de ficheros significativos (`spec-*.md`, `adr-*.md`, `sprint-status.yaml`),
   para que el agente sepa dónde están (evita cientos de filas inútiles, pero mantiene
   las referencias que de verdad importan).
6. El script **solo reescribe** `INDICE.md` si hay cambios reales (descarta la línea
   `actualizado` al comparar) → no ensucia `git status`.

> **Regla de oro**: la descripción auto-detectada (del frontmatter `description:` o del
> `#` título) es un punto de partida, casi nunca suficiente. El valor está en el
> **catálogo manual `.index-desc.md`** con descripciones pensadas para el agente.

## Cómo se invoca / cuándo se dispara

- **Cada cambio de doc**: cuando se crea, renombra, mueve o elimina una documentación,
  volver a indexar. Integrable al cierre de sesión (skill `sesion-bitacora`).
- **Bajo demanda**: "indexa la doc", "actualiza el índice", "regenera INDICE.md".

## Procedimiento

1. **Localizar/copiar el script** si no está en el repo: `scripts/index_docs.py` (o en
   `.opencode/skills/indexa-docs/scripts/index_docs.py`).
2. **Detectar las entradas**:
   ```bash
   python3 <index_docs.py> --list "<dir>"   # lista rutas y descripciones actuales
   ```
   o ejecutar regen completo: `python3 <index_docs.py> "<dir>" "<título>"`.
3. **Escribir/mejorar el catálogo `.index-desc.md`** (la fuente de descripciones):
   para cada doc relevante, una entrada con descripción **semántica y orientada a**
   *qué contiene + cuándo abrirlo*:
   ```text
   docs/network-inventory.md — LAN+Tailscale+hardware/so por equipo; consultar para IPs, MACs, estado de máquinas
   docs/roberto-ai-kernel-plan.md — plan+estado de la actualización del kernel; leer antes de instalar kernel/GPU en roberto-ai
   sesiones/sesion-2026-09-13.md — log del trabajo del día
   _bmad-output/specs/ — especificaciones por historia; fuente de verdad de qué se construye
   _bmad-output/adr/ — decisiones de arquitectura aceptadas y su contexto
   ```
4. **Regenerar** el índice y **revisar** el `INDICE.md` resultante:
   - Cada entrada con descripción que aporta contexto (no solo el título del fichero).
   - Categorías: `raiz`, `docs`, `sesiones`, `bmad:_bmad-output`, `bmad:.bmad`.
   - Que no aparezcan `node_modules/`, `.git/`, `.agents/`, `.opencode/`, caches.
5. **No commitear ni pushear** salvo petición explícita.

## Catálogo manual (fuente principal de descripciones)

`.index-desc.md` en la raíz del repo, **una entrada por doc** (1-2 líneas si aporta):

```text
ruta — qué contiene y cuándo consultarlo
```

Tiene prioridad absoluta sobre la auto-detección. Es donde se consigue la **ganancia
semántica**: dedicar tiempo aquí a escribir bien cada descripción, porque es lo que el
agente usará para decidir si vale la pena abrir el doc.

## Integración con sesion-bitacora

Al **cerrar sesión**, además de generar `sesiones/` y `next.md`, revisar si se tocó
alguna doc durante la sesión y, si es así, re-indexar y actualizar `.index-desc.md`
con las descripciones nuevas/mejoradas.

## Instalación en repos

Junto con BMAD / `sesion-bitacora`, copiar esta skill (incl. `scripts/index_docs.py`)
a `.opencode/skills/indexa-docs/` del repo nuevo. Reiniciar opencode tras instalarla.
