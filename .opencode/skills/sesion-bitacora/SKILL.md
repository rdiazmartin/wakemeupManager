---
name: sesion-bitacora
description: Use ONLY when opening or closing a work session in a repo configured for session logging. On open it reads the latest session consolidation file (sesiones/sesion-YYYY-MM-DD.md) and next.md to restore context; on close it generates both a consolidated session file in sesiones/ and a refreshed next.md. Trigger on phrases like "abre sesión", "cierra sesión", "apertura/cierre de sesión", "bitácora", or when loading context at session start (next.md, sesiones/). Install in every new repo alongside BMAD.
---

# Bitácora de sesiones y próximas acciones (next.md)

Skill para **mantener la bitácora de trabajo por sesión y las próximas acciones**
de un repo. Asegura que al **abrir** una sesión se recupere el contexto previo y
al **cerrar** se registre el resumen y se consoliden los pendientes en `next.md`.

Instalar de serie en cada **repo nuevo que se cree junto con BMAD** (ver skill
`new-bmad-repo`), además de usarla en los repos existentes que la adopten.

## Contratos de ficheros

Convenciones fijas de esta skill:

| Fichero | Ruta | Rol |
|---|---|---|
| Bitácora de sesión | `sesiones/sesion-YYYY-MM-DD.md` | Consolidación de UN día de trabajo (una por día) |
| Próximas acciones | `next.md` | Pendientes lógicos consolidados para retomar al reiniciar |

- **Nombrado de bitácoras**: marca de tiempo `YYYY-MM-DD` (una por día, se
  reescribe si se trabaja varias veces el mismo día). Ej. `sesiones/sesion-2026-09-13.md`.
- Si un día ya existe bitácora, **añadir/editar** esa misma, no crear otra.
- La "última bitácora" es el fichero con la **fecha más reciente** en `sesiones/`
  (ordenar por nombre; el formato `YYYY-MM-DD` ordena bien).
- `next.md` y la bitácora siempre en **raíz del repo**/`sesiones/` respectivamente.

## Apertura de sesión (OPEN)

Al iniciar el trabajo (usuario dice "abre sesión", pide contexto, o simplemente
se comienza el trabajo y se cree útil) hacer **en orden**:

1. **Localizar la última bitácora**: listar `sesiones/` y tomar el fichero con la
   fecha mayor entre `sesion-*.md`. Leerlo.
2. **Leer `next.md`** (si existe).
3. **Resumir al usuario** en una frase: última sesión (fecha) y los pendientes
   de `next.md` más relevantes (prioridad alta o marcados como "sigue pendiente").
4. **No crear** aún la bitácora del día actual: se crea **solo al cerrar** (o si
   hay algo sustancial que registrar y el usuario lo pide).
5. Si `next.md` no existe o está vacío y el repo tiene trabajo previo, avisar y
   proponer crearlo al cerrar.

Criterio de disparo auto: cuando se mencionen `next.md`, `sesiones/`, "bitácora",
o al cargar contexto al inicio de sesión de agente en un repo con esta skill.

## Cierre de sesión (CLOSE)

Al terminar el trabajo (usuario dice "cierra sesión" / "cierre de sesión", o se va
a reiniciar / finalizar la sesión de agente) hacer:

1. **Generar/actualizar la bitácora del día** `sesiones/sesion-<YYYY-MM-DD>.md`:
   - Si no existe, crearla con la fecha de hoy y esta plantilla.
   - Si existe (mismo día), **añadir/editar** los apartados correspondientes.
   - Incluir un **resumen en la cabecera** (qué se hizo hoy, en una frase).
2. **Consolidar `next.md`**: reescribir/ordenar los pendientes lógicos detectados
   durante la sesión, actualizando el bloque de cabecera (ver plantilla).
3. **Registrar la referencia cruzada**: en la cabecera de `next.md` indicar que se
   consolidó al cerrar la sesión del día; en la bitácora apuntar a los `next.md`
   relevantes si aplica.
4. **NO commitear ni pushear** salvo que el usuario lo pida explícitamente.

### Plantilla de bitácora (`sesiones/sesion-YYYY-MM-DD.md`)

```markdown
# Sesión 2026-<MM>-<DD>

> Fecha real · resumen breve de la sesión (qué se logró).

## Logro

- resumen de lo hecho (máx. ~3-5 bullets)

## Próximas acciones identificadas

- [ ] pendiente detectado en esta sesión (para consolidar en next.md)
- [ ] otra pendiente

## Notas / decisiones

- decisiones, aclaraciones, contexto que se cierra en esta sesión

## Referencias cruzadas

- next.md generado/actualizado en la sesión
```

### Plantilla de `next.md`

```markdown
# Próximas acciones (next actions)

> Consolidado al cierre de la sesión del <YYYY-MM-DD>.
> Pendientes lógicos detectados para retomar tras reinicio.

## <Tema/área 1>

- [ ] **tarea pendiente** (prioridad entre paréntesis) con detalle en el mismo item.

## <Otro tema/área>

- [ ] ...
```

Estructurar `next.md` por **áreas temáticas** (encabezados `##`), marcando
`- [x]` lo hecho en la sesión actual y `- [ ]` los pendientes, con los de mayor
prioridad arriba. Limpiar los `- [x]` que ya no aportan contexto al final.

## Instalación en repos nuevos

Junto con la instalación de **BMAD Method** en un repo nuevo (skill
`new-bmad-repo`, paso 5/6), copiar esta skill para que quede disponible:

```bash
REPO=<ruta/repo>
mkdir -p "$REPO/.opencode/skills"
cp -r "$HOME/repos/roberto-lan/.opencode/skills/sesion-bitacora" \
      "$REPO/.opencode/skills/"
```

- Queda versionada con el repo y se auto-carga como skill de proyecto.
- Tras copiarla, **reiniciar opencode** en ese repo para que la cargue.

## Directrices

- Respetar el nombrado `YYYY-MM-DD`; nunca sobrescribir bitácoras de otros días.
- El resumen de cabecera de la bitácora debe ser legible de un vistazo.
- No inventar trabajo no realizado; reflejar solo lo que consta en la sesión.
- No tocar `git` (ni commit ni push) salvo petición explícita del usuario.
