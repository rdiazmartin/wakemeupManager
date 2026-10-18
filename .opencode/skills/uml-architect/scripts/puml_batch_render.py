#!/usr/bin/env python3
"""puml_batch_render.py — renderiza por lotes TODOS los bloques PlantUML de un fichero
(o de un directorio de `.puml`/`.md`) a imágenes PNG, una por diagrama.

El nombre de cada PNG lo declara el propio diagrama en su primera línea:
    @startuml 01_componentes     ->  01_componentes.png
(si no declara nombre, se usa `NN_diagrama.png`).

Renderizado LOCAL por defecto (nada de mandar código a terceros):
  1. binario NATIVO de PlantUML  (sin Java, sin Docker)   <- preferido
  2. `plantuml.jar` + un `java`  (del sistema o JAVA_BIN)
  3. Docker (`plantuml/plantuml`)
  4. Kroki — SOLO si se pide explícitamente (`--backend kroki` o KROKI_PLANTUML_URL)

Uso:
  puml_batch_render.py <fichero_o_directorio> [dir_salida] [opciones]

Opciones:
  --backend auto|native|jar|docker|kroki   (def. auto)
  --jar RUTA        plantuml.jar concreto
  --java RUTA       binario java concreto
  --cache DIR       caché de binarios (def. ~/.cache/plantuml)
  --list            solo lista los diagramas que encuentra (no renderiza)
  --keep-sources    deja los .puml junto a los .png (def. sí se dejan)
  --no-keep-sources no deja los .puml

Códigos de salida: 0 todo OK · 1 uso/entrada inválida · 2 algún diagrama falló.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

PUML_VERSION = "1.2026.9"
NATIVE_URL = ("https://github.com/plantuml/plantuml/releases/download/"
              f"v{PUML_VERSION}/native-plantuml-linux-amd64-{PUML_VERSION}.zip")
JAR_URL = ("https://github.com/plantuml/plantuml/releases/download/"
           f"v{PUML_VERSION}/plantuml.jar")

# @startuml [nombre] ... @enduml   (captura el bloque completo y el nombre)
PATRON = re.compile(r"@startuml(?:[ \t]+([^\r\n]+))?[ \t]*\r?\n.*?@enduml",
                    re.DOTALL | re.IGNORECASE)


def sanitizar(nombre: str | None, indice: int) -> str:
    if not nombre or not nombre.strip():
        return f"{indice:02d}_diagrama"
    limpio = re.sub(r"[^\w\-.]+", "_", nombre.strip().strip("\"'"))
    return re.sub(r"\.(png|svg|puml)$", "", limpio, flags=re.I) or f"{indice:02d}_diagrama"


def leer_bloques(ruta: Path) -> list[tuple[str, str, Path]]:
    """Devuelve [(nombre, bloque, fichero_origen)] de un fichero o directorio."""
    if ruta.is_dir():
        ficheros = sorted([p for p in ruta.rglob("*")
                           if p.is_file() and p.suffix.lower() in (".puml", ".md", ".txt")])
    else:
        ficheros = [ruta]
    salida: list[tuple[str, str, Path]] = []
    for f in ficheros:
        texto = f.read_text(encoding="utf-8", errors="replace")
        for idx, m in enumerate(PATRON.finditer(texto), start=1):
            salida.append((sanitizar(m.group(1), len(salida) + 1), m.group(0).strip(), f))
    return salida


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------
def _descargar(url: str, destino: Path) -> None:
    print(f"· descargando {url.rsplit('/', 1)[-1]} …", file=sys.stderr)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=180) as r:
        destino.write_bytes(r.read())


def backend_native(cache: Path) -> str | None:
    binario = cache / "native" / "plantuml"
    if not binario.is_file():
        z = cache / "native.zip"
        try:
            _descargar(NATIVE_URL, z)
            with zipfile.ZipFile(z) as zf:
                zf.extractall(cache / "native")
            z.unlink(missing_ok=True)
        except Exception as e:                                   # noqa: BLE001
            print(f"  (native no disponible: {e})", file=sys.stderr)
            return None
    if binario.is_file():
        binario.chmod(0o755)
        return str(binario)
    return None


def backend_jar(cache: Path, jar: str | None, java: str | None) -> tuple[str, str] | None:
    javabin = java or os.environ.get("JAVA_BIN") or shutil.which("java")
    if not javabin:
        return None
    jarpath = Path(jar) if jar else cache / "plantuml.jar"
    if not jarpath.is_file():
        try:
            _descargar(JAR_URL, jarpath)
        except Exception as e:                                   # noqa: BLE001
            print(f"  (jar no disponible: {e})", file=sys.stderr)
            return None
    return (javabin, str(jarpath))


def backend_docker() -> list[str] | None:
    if not shutil.which("docker"):
        return None
    try:
        subprocess.run(["docker", "info"], capture_output=True, timeout=10, check=True)
    except Exception:                                            # noqa: BLE001
        return None
    return ["docker", "run", "--rm", "-v"]


def elegir_backend(opt: str, cache: Path, jar: str | None, java: str | None):
    intentos = ["native", "jar", "docker"] if opt == "auto" else [opt]
    for b in intentos:
        if b == "native":
            exe = backend_native(cache)
            if exe:
                return ("native", exe)
        elif b == "jar":
            par = backend_jar(cache, jar, java)
            if par:
                return ("jar", par)
        elif b == "docker":
            if backend_docker():
                return ("docker", None)
        elif b == "kroki":
            return ("kroki", os.environ.get("KROKI_PLANTUML_URL", "https://kroki.io/plantuml/png"))
    return (None, None)


def _ejecutar(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


def render_uno(backend: str, medio, puml: Path, out: Path) -> tuple[bool, str]:
    """Renderiza un .puml a PNG (junto a él). Devuelve (ok, detalle)."""
    png = puml.with_suffix(".png")
    if backend == "native":
        cmd = [medio, "-tpng", "-nbthread", "1", str(puml)]
    elif backend == "jar":
        java, jar = medio
        cmd = [java, "-Djava.awt.headless=true", "-jar", jar, "-tpng", "-nbthread", "1", str(puml)]
    elif backend == "docker":
        cmd = ["docker", "run", "--rm", "-v", f"{out}:/data", "plantuml/plantuml",
               "-tpng", f"/data/{puml.name}"]
    else:  # kroki
        cuerpo = puml.read_text(encoding="utf-8")
        req = urllib.request.Request(
            medio, data=cuerpo.encode("utf-8"), method="POST",
            headers={"Content-Type": "text/plain; charset=utf-8",
                     "User-Agent": "puml-batch-render/2.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:   # noqa: S310
                png.write_bytes(r.read())
            return (True, "")
        except urllib.error.HTTPError as e:
            return (False, e.read().decode("utf-8", errors="replace").strip()[:800])
        except Exception as e:                                   # noqa: BLE001
            return (False, str(e))

    # 1) validar sintaxis: -checkonly devuelve != 0 y el diagrama NO se genera
    check = _ejecutar([c.replace("-tpng", "-checkonly") if c == "-tpng" else c
                       for c in cmd if c not in ("-nbthread", "1")])
    if check.returncode != 0:
        # el mensaje útil (con «Error line N in file: …») lo da el render: lo ejecutamos
        res = _ejecutar(cmd)
        detalle = (res.stdout + res.stderr + check.stdout + check.stderr).strip().replace("\n", " | ")
        png.unlink(missing_ok=True)          # no dejar la imagen de error
        return (False, detalle[:600] or f"checkonly rc={check.returncode}")

    # 2) render
    res = _ejecutar(cmd)
    if res.returncode != 0 or not png.is_file():
        detalle = (res.stdout + res.stderr).strip().replace("\n", " | ")
        return (False, detalle[:400] or f"rc={res.returncode}")
    return (True, f"{png.stat().st_size // 1024} KB")


def main() -> int:
    ap = argparse.ArgumentParser(description="Renderiza todos los bloques @startuml de un fichero/directorio a PNG.")
    ap.add_argument("entrada", type=Path)
    ap.add_argument("salida", nargs="?", type=Path, default=Path("./uml_output"))
    ap.add_argument("--backend", default="auto", choices=["auto", "native", "jar", "docker", "kroki"])
    ap.add_argument("--jar")
    ap.add_argument("--java")
    ap.add_argument("--cache", type=Path,
                    default=Path(os.environ.get("PLANTUML_CACHE", Path.home() / ".cache" / "plantuml")))
    ap.add_argument("--list", action="store_true", dest="solo_listar")
    ap.add_argument("--keep-sources", action="store_true", default=True)
    ap.add_argument("--no-keep-sources", action="store_false", dest="keep_sources")
    args = ap.parse_args()

    if not args.entrada.exists():
        print(f"Error: no existe '{args.entrada}'", file=sys.stderr)
        return 1

    bloques = leer_bloques(args.entrada)
    if not bloques:
        print(f"No he encontrado bloques @startuml…@enduml en '{args.entrada}'", file=sys.stderr)
        return 1

    print(f"· {len(bloques)} diagrama(s) encontrados en '{args.entrada}':")
    for nombre, _, origen in bloques:
        print(f"    - {nombre}.png   (de {origen.name})")
    if args.solo_listar:
        return 0

    backend, medio = elegir_backend(args.backend, args.cache, args.jar, args.java)
    if not backend:
        print("Error: no hay backend de render disponible (native/jar/docker).\n"
              "       Instala Java, o usa --backend kroki (envía el código a un servicio externo).",
              file=sys.stderr)
        return 1
    if backend == "kroki":
        print(f"⚠️  backend KROKI ({medio}): el diagrama se envía a un servicio EXTERNO. "
              "No lo uses con código privado.", file=sys.stderr)
    print(f"· backend: {backend}")

    args.salida.mkdir(parents=True, exist_ok=True)
    ok, fallos = 0, []
    for nombre, bloque, _origen in bloques:
        puml = args.salida / f"{nombre}.puml"
        # se fuerza el nombre declarado para que PlantUML nombre el PNG igual
        cuerpo = re.sub(r"^@startuml[^\n]*", f"@startuml {nombre}", bloque, count=1, flags=re.I)
        puml.write_text(cuerpo + "\n", encoding="utf-8")
        bien, detalle = render_uno(backend, medio, puml, args.salida)
        if bien:
            ok += 1
            print(f"[OK]    {args.salida / (nombre + '.png')}  {detalle}")
            if not args.keep_sources:
                puml.unlink(missing_ok=True)
        else:
            fallos.append((nombre, detalle))
            print(f"[ERROR] {nombre}: {detalle}", file=sys.stderr)

    print(f"\nResumen: {ok}/{len(bloques)} PNG generados en '{args.salida.resolve()}'")
    if fallos:
        print("Diagramas con error (corrige el PlantUML y vuelve a ejecutar):", file=sys.stderr)
        for nombre, detalle in fallos:
            print(f"  - {nombre}: {detalle}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
