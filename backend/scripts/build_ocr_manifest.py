"""Construye un manifest OCR reproducible desde un vendor ya preparado.

No descarga ni ejecuta nada. Cada archivo se asigna a un cierre (Poppler o
Paddle), conserva tamaño/hash y queda sujeto a límites explícitos del release.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from cubicador.toolchain import _sha, _reparse


TOOLS = {
    "poppler.pdftotext": "poppler/bin/pdftotext.exe",
    "poppler.pdfinfo": "poppler/bin/pdfinfo.exe",
    "poppler.pdftoppm": "poppler/bin/pdftoppm.exe",
    "paddle.runner": "paddle/bin/paddle_ocr_runner.exe",
}


def build_manifest(vendor: Path, *, poppler_version: str, paddle_version: str,
                   max_files: int, max_bytes: int, max_component_files: int,
                   max_component_bytes: int) -> dict:
    manifest_path = vendor / "toolchain-manifest.json"
    pin_path = vendor / "toolchain-manifest.sha256"
    if _reparse(vendor) or _reparse(manifest_path) or _reparse(pin_path):
        raise ValueError("La raíz o metadata del vendor no puede ser reparse point")
    assets = [p for p in sorted(vendor.rglob("*"))
              if p.is_file() and p not in (manifest_path, pin_path)]
    if not assets or any(_reparse(p) for p in assets):
        raise ValueError("Inventario vacío o contiene symlinks")
    for value in (max_files, max_bytes, max_component_files, max_component_bytes):
        if value <= 0: raise ValueError("Los límites deben ser positivos")
    if len(assets) > max_files or sum(p.stat().st_size for p in assets) > max_bytes:
        raise ValueError("Vendor excede límites globales")
    files = {}
    components: dict[str, dict] = {}
    for component, version, root_name in (("poppler", poppler_version, "poppler"), ("paddle", paddle_version, "paddle")):
        members = [p for p in assets if p.relative_to(vendor).parts[0] == root_name]
        if not members:
            raise ValueError(f"Componente vacío: {component}")
        if len(members) > max_component_files or sum(p.stat().st_size for p in members) > max_component_bytes:
            raise ValueError(f"Componente excede límites: {component}")
        names = []
        for path in members:
            relative = path.relative_to(vendor).as_posix()
            names.append(relative)
            files[relative] = {"sha256": _sha(path), "size": path.stat().st_size, "component": component}
        components[component] = {"version": version, "files": names,
                                 "limits": {"max_files": max_component_files, "max_bytes": max_component_bytes}}
    if len(files) != len(assets):
        raise ValueError("Cada asset debe pertenecer a poppler o paddle")
    if any(path not in files for path in TOOLS.values()):
        raise ValueError("Faltan herramientas obligatorias")
    return {"schema_version": 2,
            "release": {"poppler_version": poppler_version, "paddle_version": paddle_version},
            "limits": {"max_files": max_files, "max_bytes": max_bytes},
            "components": components, "tools": TOOLS, "files": files}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", type=Path, required=True)
    parser.add_argument("--poppler-version", required=True)
    parser.add_argument("--paddle-version", required=True)
    parser.add_argument("--max-files", type=int, default=20_000)
    parser.add_argument("--max-bytes", type=int, default=4 * 1024 * 1024 * 1024)
    parser.add_argument("--max-component-files", type=int, default=15_000)
    parser.add_argument("--max-component-bytes", type=int, default=3 * 1024 * 1024 * 1024)
    parser.add_argument("--write", action="store_true", help="Sin esta opción solo imprime el manifest")
    parser.add_argument("--write-pin", action="store_true", help="Escribe sidecar SHA-256 de metadata")
    args = parser.parse_args()
    try:
        vendor = args.vendor.resolve(strict=True)
        manifest = build_manifest(vendor, poppler_version=args.poppler_version,
                                  paddle_version=args.paddle_version, max_files=args.max_files,
                                  max_bytes=args.max_bytes, max_component_files=args.max_component_files,
                                  max_component_bytes=args.max_component_bytes)
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    manifest_path = vendor / "toolchain-manifest.json"
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if args.write:
        if _reparse(manifest_path): raise SystemExit("Manifest destino no confiable")
        manifest_path.write_bytes(encoded)
    if args.write_pin:
        if not args.write: raise SystemExit("--write-pin requiere --write")
        # El sidecar no forma parte del inventario y sirve como artefacto de
        # release para alimentar el pin inmutable del código/instalador.
        import hashlib
        pin_path.write_text(hashlib.sha256(encoded).hexdigest() + "\n", encoding="ascii")
    print(encoded.decode(), end="")
    return 0


if __name__ == "__main__": raise SystemExit(main())
