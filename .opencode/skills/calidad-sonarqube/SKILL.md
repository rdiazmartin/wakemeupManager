---
name: calidad-sonarqube
description: Use when deciding WHAT to fix in a codebase — triaging SonarQube issues/hotspots, sizing a refactor, or judging whether a file is dangerous to change. Core criterion: complexity × edit frequency × presence of tests ("the oracle"); the metric value alone does NOT order the work. Covers how to get the data (SonarQube instance in rai, the `sonar` agent with the SonarQube MCP, `scripts/sonar/sonar-scan.sh`), why an issue is a suspicion and not a verdict (with the already-verified false positives), and the rules for a safe refactor (harness of tests first, extract with contract, gate on new code). Trigger on "sonarqube", "sonar", "deuda técnica", "complejidad", "tria los issues", "hotspots", "qué arreglo primero", "refactoriza esto", "por qué este fichero es peligroso de tocar", "calidad del código". Do NOT use to operate the SonarQube service itself (instance/scan infra: `docs/sonarqube-lab.md` in roberto-lab), nor for product bug-hunting without quality data.
---

# calidad-sonarqube — decidir qué arreglar (y qué NO) en un repo

> **Ámbito**: cualquier repo **de desarrollo**. En un repo donde el lector operativo es un
> **agente** (nadie lee ficheros de 3.000 líneas de arriba abajo), esta skill decide **el orden**
> del trabajo de calidad. Datos de la instancia del lab: `roberto-lab` → `docs/sonarqube-lab.md`.

## 0. La regla de oro: un issue es una **sospecha**, no un veredicto

Medido en `microbeVision` (2026-10-17): de los 5 hallazgos «más accionables» que propuso un agente,
**2 eran falsos positivos** y 2 más eran estilo. Ninguno de los 9 «bugs» era un bug real.

Por tanto, **nunca** propongas trabajo a partir del listado en crudo:

1. Trae los hallazgos (`OPEN`, del proyecto correcto).
2. **Lee el código** del sitio señalado antes de calificarlo (`read`/`grep`). Si dudas de la regla,
   pide el detalle de la regla (herramienta `show_rule` del MCP).
3. Clasifica cada uno: **real**, **discutible** o **falso positivo**, y di **por qué** citando
   `fichero:línea`.
4. Si es FP → **no toques el código para contentar a la herramienta**. Documéntalo y sigue.

## 1. Cómo priorizar: **complejidad × frecuencia de edición × ¿hay tests?** (el oráculo)

El **valor de la métrica NO ordena el trabajo**. Ordena el cruce:

> 🔴 **complejo + muy editado + SIN tests** → primero · 🟡 complejo con tests → al tocarlo ·
> ⚪ complejo pero estable (1 commit) → latente, no urgente · ⚪ nits mecánicos → al final

**Por qué**: la complejidad no encarece «entender», **rompe el oráculo**. Una función de
complejidad 438 en un UI Compose no es testeable → quien cambia y quien revisa aprueban con
**confianza falsa**. Y el coste se paga **en cada edición**: contexto que hay que cargar, riesgo de
anclar mal el parche en texto que se parece, e invariantes que no se pueden verificar.

Mídelo (no lo estimes):

```bash
# frecuencia de edición del fichero (churn)
git log --since=90.days --oneline -- <fichero> | wc -l

# ¿tiene oráculo?
git ls-files | grep -iE '<nombre>(Test|_test)'
```

Caso real: `RootScreen.kt` (complejidad **438**, **80 commits**, **sin tests**) y
`FloatingPanel.kt` (**86**, **42 commits**, **sin tests**) iban **muy por delante** de 70 nits de
estilo. Los 122 commits sobre dos ficheros sin verificación automática es el verdadero riesgo.

## 2. El oráculo manda: primero tests, luego refactor

Sin red no se refactoriza. El orden **obligatorio**:

1. **Harness de tests primero** (en UI: tests por estado — cargando / sin cámara / con muestra /
   error). Sin eso, trocear es mover el entrelazado de sitio y seguir a ciegas.
2. **Trocear «con contrato»**: cada unidad extraída con **parámetros explícitos** (nada de estado
   global nuevo) y **su test**.
3. **Gate sobre código nuevo** (periodo «new code»), no como limpieza de legacy: es la única forma
   de que el siguiente ciclo no lo reintroduzca.

## 3. El matiz que evita el error clásico

Para un agente lo caro **no es «largo»**, es **entrelazado**: estado mutable compartido, orden
implícito, ramas que se parecen. Un `when` plano y largo puede ser *más barato* de editar que 15
funciones encadenadas (cada indirección es otro fichero que cargar).

- Objetivo: **verificabilidad local** (el contrato cabe en una pantalla y tiene test), **no bajar
  el número**.
- **No** fragmentar por fragmentar: trocear sin contrato ni test solo mueve el problema.

## 4. De dónde salen los datos

- **Instancia**: SonarQube Community 25.12 del lab (solo lectura para el agente; perfil
  `lab-tuned`). Doc completo: `roberto-lab` → `docs/sonarqube-lab.md`.
- **MCP**: el agente **`sonar`** (solo él ve las herramientas del MCP: `search_sonar_issues_in_projects`,
  `search_security_hotspots`, `show_rule`, `get_component_measures`…) → cámbiate a él (`Tab`) o
  `opencode run --agent sonar`.
- **Escanear un repo** (si los datos están viejos): `scripts/sonar/sonar-scan.sh <project-key> [repo-dir]`
  desde el repo del lab (instala el escáner solo; el token se resuelve del **vault**, nunca del repo).
- ⛔ **Nunca** trabajar el proyecto `hcdsns_v10.3` (SESCAM/trabajo).

## 5. Falsos positivos ya verificados (no los reportes como bugs)

- `python:S3516` («función que siempre devuelve lo mismo») en los `main()` de CLIs **de
  diagnóstico**: devuelven 0 a propósito.
- `python:S1515` («late binding de variable de bucle») cuando el closure se invoca **dentro de la
  misma iteración** (p. ej. `executor.map(pase, range(2))`): inocuo.
- `python:S2612` con `chmod 0o755/0o644` deliberados; `python:S5852` con regex **ancladas**;
  `python:S1764` con el idioma NaN (`v != v`); `python:S5443` / `kotlin:S1313` en tests.
- **Código generado** (`*_pb2_grpc.py`, `*.pb.go`, `*.java` de gRPC): se **excluye**, no se arregla.
- Nombrado/duplicación de literales (`kotlin:S100`, `python:S100/S116`, `*:S1192`): desactivados a
  propósito en el perfil (Compose usa PascalCase).
- Funciones vacías que **implementan una interfaz** (`kotlin:S1186`): legítimas.
- **Hotspots de seguridad** (`S6471` Dockerfile como root, cleartext HTTP, `S5443`, `S2612`): son
  **decisiones del equipo**, no arreglos mecánicos.

## 6. Formato del informe

Tabla corta (`regla | severidad | fichero:línea | veredicto | acción`) y **como máximo 5 arreglos
priorizados con su diff**, diciendo **en una línea por qué ese orden** (p. ej. «complejidad 438,
80 commits, sin tests»). Nada de volcar el JSON del MCP.

## 7. Anti-patrones (los que ya nos han mordido)

- Proponer arreglos por el listado en crudo, sin leer el código → **falsos positivos con seguridad**.
- «Refactorizar lo complejo» empezando por el valor más alto → puede ser un fichero que **nadie
  toca** (1 commit): latente, no urgente.
- Bajar el número de la métrica como objetivo → **trocear sin contrato ni tests**: el entrelazado
  se muda y encima se añaden indirecciones.
- Arreglar antes los nits (imports, valores sin leer, null-checks): son **cosméticos** y se
  regeneran solos; nunca por delante del oráculo.
- Tocar el código para callar a la herramienta cuando el hallazgo es intencional.

## 8. Despliegue

- Fuente única: este `SKILL.md` (repo `roberto-lab`, `.opencode/skills/calidad-sonarqube/`) y su
  copia publicada en **`skills-repo/catalog/calidad-sonarqube/`**.
- Instalación en un repo de desarrollo (junto a `sesion-bitacora` e `indexa-docs`):
  `cp -r <roberto-lab>/.opencode/skills/calidad-sonarqube <repo>/.opencode/skills/` (queda
  versionado con el repo) — o global en `~/.config/opencode/skills/` para todas las sesiones.
- El agente **`sonar`** se despliega aparte (`scripts/opencode/install-agent.sh`, global a la
  máquina); si el MCP no está montado, esta skill sigue aportando el criterio y los comandos.
