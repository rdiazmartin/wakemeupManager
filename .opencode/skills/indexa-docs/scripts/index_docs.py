#!/usr/bin/env python3
"""index_docs.py — genera un INDICE.md maestro único optimizado para lectura por IA.

Objetivo: producir un índice MÍNIMO y RÁPIDO de leer, no una tabla para humanos.

- Una línea por doc:  "ruta — descripción".
- Frontmatter que resume sin leer el cuerpo (titulo, categorias, entradas, fecha).
- Doc "nativa" (raíz, docs/, sesiones/, cualquier subcarpeta con *.md) lista archivo
  a archivo.
- Doc BMAD (_bmad-output/, .bmad/) se indexa por RUTAS/CARPETAS CLAVE, no por archivo
  (puede haber cientos).
- Solo escribe el índice si hay cambios reales respecto al previo (evita ruido de git).

Uso:
    index_docs.py [directorio] [titulo]

Descripciones:
  - Si el doc tiene frontmatter con 'description:' se usa esa línea.
  - Si no, se usa el primer encabezado '# ' del fichero.
  - Un catálogo manual opcional ".index-desc.md" en la raíz del repo permite fijar
    descripciones estables ("ruta — descripción" por línea), que tienen prioridad.
"""
from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

# Dir de entornos/herramientas: se ignoran en cualquier profundidad (no son doc).
EXCLUDE_DIRS = {
    "node_modules", ".git", ".agents", ".opencode",
    ".venv", "venv", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".cache",
    "__pycache__", ".hypothesis", ".eggs",
}
# Doc BMAD REAL de proyecto (indexar por carpetas clave). _bmad/ es plantillas de la
# herramienta (no doc) → no se lista.
BMAD_DIRS = ["_bmad-output"]
NATIVE_TOPDIRS = ["docs", "sesiones"]
# Subdirs de raíz que NUNCA se tratan como doc (plantillas/herramienta), indistinguibles por nombre.
TOOL_TOPDIRS = {"_bmad", "_bmad-output", ".bmad"}
# ficheros clave dentro de un dir BMAD que merecen entrada individual
BMAD_KEY_FILES = ("spec-*.md", "sprint-status.yaml", "adr-*.md", "epic-*.md")

# Más de este nº de ficheros en una carpeta ⇒ colapsar en una sola entrada
# (relevante para sesiones/ y similares), salvo descripción manual explícita.
COLLAPSE_THRESHOLD = 15


def parse_frontmatter_description(path: Path) -> str | None:
    """Devuelve la línea 'description:' del frontmatter del fichero, si existe."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(2000)
    except OSError:
        return None
    m = re.search(r"^description:\s*(.+)$", head, re.MULTILINE)
    return m.group(1).strip() if m else None


def parse_title(path: Path) -> str | None:
    """Primer encabezado '# ' del fichero (fallback de descripción)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("# ") and not line.startswith("## "):
                    return line[2:].strip()
                if not line:
                    continue
    except OSError:
        pass
    return None


def load_manual_catalog(root: Path) -> dict[str, str]:
    """Carga '.index-desc.md' con 'ruta — descripción' (catálogo de descripciones)."""
    cat = {}
    f = root / ".index-desc.md"
    if not f.is_file():
        return cat
    try:
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.rstrip()
            if "—" in line:
                path, _, desc = line.partition("—")
                cat[path.strip()] = desc.strip()
    except OSError:
        pass
    return cat


def is_bmad(rel: Path) -> bool:
    return any(rel.parts[0] == d for d in BMAD_DIRS) if rel.parts else False


def collect_docs(root: Path, manual: dict[str, str]) -> tuple[list[tuple[str, list[str]]], int]:
    """Recopila secciones (cabecera → líneas). Devuelve (secciones, entradas)."""

    def describe(rel: str, path: Path) -> str:
        if rel in manual:
            return manual[rel]
        name = Path(rel).name
        txt = parse_frontmatter_description(path) or parse_title(path) or ""
        txt = txt.strip()
        # si la descripción repite el nombre del fichero ("X.md — algo"), quitar el prefijo
        if txt.startswith(name):
            rest = txt[len(name):].lstrip(" —-")
            if rest:
                txt = rest
        return txt

    secciones: list[tuple[str, list[str]]] = []  # (cabecera, líneas)
    entradas = 0

    # --- raíz (docs sueltos) ---
    root_mds = sorted(
        p for p in root.glob("*.md")
        if p.is_file() and p.name != "INDICE.md" and p.name != ".index-desc.md"
    )
    if root_mds:
        lineas = []
        for p in root_mds:
            lineas.append(f"{p.name} — {describe(p.name, p)}")
            entradas += 1
        secciones.append(("raiz", lineas))

    # --- subcarpetas de doc nativa ---
    subdirs: list[str] = []
    seen: set[str] = set()
    for d in NATIVE_TOPDIRS:
        if (root / d).is_dir():
            subdirs.append(d)
            seen.add(d)
    for d in sorted(p.name for p in root.iterdir() if p.is_dir()):
        if (
            d.startswith(".")
            or d in seen
            or d in EXCLUDE_DIRS
            or d in TOOL_TOPDIRS
        ):
            continue
        # carpeta con algún *.md no excluido == nativa
        if any(p.suffix == ".md" for p in (root / d).rglob("*.md")):
            subdirs.append(d)
    for d in subdirs:
        mds = []
        for p in (root / d).rglob("*.md"):
            if any(part in EXCLUDE_DIRS for part in p.parts) or p.name == "INDICE.md":
                continue
            rel = p.relative_to(root).as_posix()
            if rel in manual and not manual.get(rel):
                continue
            mds.append(p)
        if not mds:
            continue
        # Colapsa carpetas con muchos ficheros (p.ej. sesiones/: N bitácoras) en una
        # sola entrada: listar 40 bitácoras no aporta, saber dónde están sí.
        if len(mds) > COLLAPSE_THRESHOLD:
            rel = (root / d).as_posix().rstrip("/") + "/"
            n = len(mds)
            desc = manual.get(rel) or f"{n} ficheros de {d}; consultar el/los de la fecha de interés"
            secciones.append((d, [f"{rel} — {desc}"]))
            entradas += 1
            continue
        lineas = []
        for p in sorted(mds):
            rel = p.relative_to(root).as_posix()
            lineas.append(f"{rel} — {describe(rel, p)}")
            entradas += 1
        secciones.append((d, lineas))

    # --- doc BMAD: por carpetas clave, no por archivo ---
    for bd in BMAD_DIRS:
        bdir = root / bd
        if not bdir.is_dir():
            continue
        lineas = []
        # 1) subcarpetas con algo (specs, adr, ...)
        for sub in sorted(p for p in bdir.iterdir() if p.is_dir()):
            n = sum(1 for _ in sub.rglob("*"))
            lineas.append(f"{sub.relative_to(root).as_posix()}/ — {n} ficheros")
            entradas += 1
        # 2) ficheros clave que sí merecen fila
        for pat in BMAD_KEY_FILES:
            for p in sorted(bdir.rglob(pat)):
                if not p.is_file():
                    continue
                rel = p.relative_to(root).as_posix()
                lineas.append(f"{rel} — {p.name}")
                entradas += 1
        if lineas:
            secciones.append((f"bmad:{bd}", lineas))

    return secciones, entradas


def render(root: Path, titulo: str, manual: dict[str, str]) -> tuple[str, int, int]:
    secciones, entradas = collect_docs(root, manual)
    hoy = date.today().isoformat()
    categorias = len(secciones)
    out = []
    out.append("---")
    out.append("type: index-docs")
    out.append(f"titulo: {titulo}")
    out.append(f"categorias: {categorias}")
    out.append(f"entradas: {entradas}")
    out.append(f"actualizado: {hoy}")
    out.append("---")
    out.append(f"# {titulo}")
    for cab, lins in secciones:
        out.append("")
        out.append(f"## {cab}")
        out.extend(lins)
    texto = "\n".join(out) + "\n"
    return texto, categorias, entradas


def main() -> int:
    ap = argparse.ArgumentParser(description="Genera/lista INDICE.md semántico para IA.")
    ap.add_argument("ruta", nargs="?", default=".", help="directorio raíz del repo")
    ap.add_argument("titulo", nargs="?", default=None, help="título corto del repo")
    ap.add_argument(
        "--list", action="store_true",
        help="solo enumerar (ruta — descripción), no escribir el fichero",
    )
    args = ap.parse_args()

    root = Path(args.ruta).resolve()
    titulo = args.titulo or root.name
    manual = load_manual_catalog(root)

    if args.list:
        secciones, entradas = collect_docs(root, manual)
        for cab, lins in secciones:
            print(f"[{cab}]")
            for l in lins:
                print(" ", l)
        print(f"\n# {titulo}: {entradas} entradas en {len(secciones)} categorías")
        return 0

    texto, categorias, entradas = render(root, titulo, manual)
    idx = root / "INDICE.md"
    prev = ""
    if idx.is_file():
        prev = "\n".join(
            l for l in idx.read_text(encoding="utf-8").splitlines()
            if not l.startswith("actualizado:")
        )
    actual = "\n".join(
        l for l in texto.splitlines()
        if not l.startswith("actualizado:")
    )
    if prev == actual:
        print(f"· INDICE sin cambios ({categorias} categorías, {entradas} entradas)")
    else:
        idx.write_text(texto, encoding="utf-8")
        print(f"· INDICE actualizado → {idx} ({categorias} categorías, {entradas} entradas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
