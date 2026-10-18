---
name: uml-architect
description: Use when the user asks to SEE the architecture, design or structure of a repo, module or package — it analyses the source and produces a MODULAR set of PlantUML views (components, key classes, main sequence flow) rendered to PNG locally. Enforces legibility rules (2-4 views, max ~10-12 entities each) instead of one unreadable monolithic diagram, and includes a batch renderer (native PlantUML / jar+java / docker — never a third-party service by default). Trigger on "diagrama de arquitectura", "vistas UML", "dibuja la arquitectura", "cómo está estructurado este código", "diagrama de clases de", "secuencia de este flujo", "plantuml", "uml", "mapa del módulo". Do NOT use for writing design docs/specs without diagrams (use BMAD), nor for runtime/infra topology of the lab (that is docs/topologia-red-lab.md + inventory), nor to invent classes that do not exist in the code.
---

# uml-architect — vistas UML modulares desde el código (PlantUML → PNG)

> **Ámbito**: documentar la arquitectura de **código real** (repo, módulo, paquete) como un
> conjunto pequeño de diagramas legibles. El render es **local**; el código no sale de la máquina.

## Fase 0 — Alcance y lectura (no inventar)

1. Fija el objetivo: ¿repo entero, un módulo, un paquete, un flujo concreto?
2. **Lee el código de verdad** (no supongas): puntos de entrada, capas, interfaces, dependencias,
   el caso de uso principal. Si no lo has leído, no lo dibujes.
3. Si el alcance es enorme, propón dividir por módulo y empieza por el más representativo.

## Fase 1 — Partición legible (regla dura)

**Nunca un único diagrama monolítico.** Divide en **2 a 4 vistas**, con **máximo 10-12 entidades**
por vista. Si no cabe, es que hay que hacer otra vista (o subir de nivel de abstracción).

Vistas por defecto:

| Vista | Contenido |
|---|---|
| `01_componentes` | Módulos/paquetes de alto nivel y sus dependencias (quién llama a quién) |
| `02_clases_nucleo` | Clases/interfaces clave: atributos relevantes y firmas públicas; sin getters/setters triviales |
| `03_secuencia_flujo` | El flujo más importante, paso a paso (el caso de uso que explica el sistema) |

Opcionales **solo si aportan**: `04_estados` (máquina de estados), `05_datos_er` (modelo de datos),
`06_despliegue` (procesos/containers). No añadas vistas de relleno.

## Fase 2 — Escribir las especificaciones

Crea **`architecture_design.md`** (o `.puml` por vista) con un bloque por vista. **La primera línea
de cada bloque nombra la imagen resultante** (`@startuml NN_nombre`).

Estilo obligatorio al inicio de cada bloque:

```plantuml
@startuml 01_componentes
skinparam monochrome false
skinparam packageStyle rectangle
skinparam shadowing false
skinparam classAttributeIconSize 0
skinparam dpi 150
```

- Relaciones **UML estrictas**: `<|--` herencia · `<|..` implementación · `*--` composición ·
  `o--` agregación · `-->` asociación/dependencia.
- **Cierra siempre** con `@enduml`.
- Nombres de fichero `NN_tema` (minúsculas, sin espacios): el script los usa tal cual.
- En clases, anota **solo** lo que explique el diseño (no vuelques el fichero entero).

## Fase 3 — Renderizar (local)

```bash
python3 <ruta_de_esta_skill>/scripts/puml_batch_render.py architecture_design.md ./uml_output
```

- Backends (auto, en este orden): **binario nativo** de PlantUML (sin Java ni Docker) → **`plantuml.jar`
  + java** → **Docker** (`plantuml/plantuml`). Se descarga lo que falte a `~/.cache/plantuml` la
  primera vez.
- ⚠️ **Nunca** uses `--backend kroki` con código privado: manda el diagrama a un servicio externo.
- `--list` para ver el plan sin renderizar; `--no-keep-sources` si no quieres los `.puml` al lado.
- Salida por defecto `./uml_output/`; si el repo ya tiene `docs/`, propón `docs/diagramas/`.

## Fase 4 — Verificar y entregar (bucle de autocorrección)

1. Comprueba en la salida que **todos** los PNG se generaron (`[OK]` por cada uno).
2. Si alguno da `[ERROR]`, el mensaje trae **fichero y línea** (`-checkonly` de PlantUML): corrige
   el `@startuml` afectado y **vuelve a ejecutar** el script. No entregues con errores.
3. Presenta un resumen breve por vista: **ruta exacta del PNG** y **qué parte del código** documenta
   (con `fichero:línea` de referencia cuando aplique).

## Reglas

1. **Legibilidad > completitud**: mejor 3 vistas claras que 1 espagueti.
2. **No inventar**: si algo no está en el código, no aparece en el diagrama (o se marca como
   propuesta explícita, aparte).
3. **Secreto/privacidad**: render local siempre; nada de terceros salvo petición explícita.
4. Los `.puml` se conservan junto a los PNG: permiten re-renderizar y ver el cambio en `git diff`.
5. Si el usuario pide «el diagrama» en singular y el sistema tiene varias capas, explica en una
   línea por qué lo entregas como conjunto de vistas.

## Despliegue

- Fuente: `roberto-lab/.opencode/skills/uml-architect/` (este directorio) + publicación en
  `skills-repo/catalog/uml-architect/`.
- Instalación: copiar el directorio a `<repo>/.opencode/skills/` (versionado) o a
  `~/.config/opencode/skills/` (global a la máquina).
- Requisitos: `python3`. El renderer se autoabastece (nativo / jar+java / docker según la máquina).
