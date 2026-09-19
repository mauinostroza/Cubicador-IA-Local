"""Genera inventario full-tree de un vendor ya preparado; nunca descarga."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from cubicador.toolchain import _sha


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vendor", type=Path, required=True)
    parser.add_argument("--write", action="store_true", help="Sin esta opción solo imprime el manifest")
    args = parser.parse_args()
    vendor = args.vendor.resolve(strict=True); manifest_path = vendor / "toolchain-manifest.json"
    assets = [p for p in sorted(vendor.rglob("*")) if p.is_file() and p != manifest_path]
    if not assets or any(p.is_symlink() for p in assets): raise SystemExit("Inventario vacío o contiene symlinks")
    files = {p.relative_to(vendor).as_posix(): _sha(p) for p in assets}
    tools = {"poppler.pdftotext": "poppler/bin/pdftotext.exe", "poppler.pdfinfo": "poppler/bin/pdfinfo.exe",
             "poppler.pdftoppm": "poppler/bin/pdftoppm.exe", "paddle.runner": "paddle/bin/paddle_ocr_runner.exe"}
    if any(path not in files for path in tools.values()): raise SystemExit("Faltan herramientas obligatorias")
    manifest = {"schema_version": 1, "tools": tools, "files": files}
    encoded = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if args.write: manifest_path.write_bytes(encoded)
    print(encoded.decode(), end="")
    return 0


if __name__ == "__main__": raise SystemExit(main())
