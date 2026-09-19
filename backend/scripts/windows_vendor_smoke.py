"""Offline release-contract smoke for the Windows CI runner.

Uses tiny fixture payloads only; it never enables the production pin or OCR.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from cubicador.toolchain import ToolchainError, TrustedToolchain, _sha
from build_ocr_manifest import build_manifest


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cubicador-vendor-smoke-") as raw:
        root = Path(raw)
        required = (
            "poppler/bin/pdftotext.exe", "poppler/bin/pdfinfo.exe", "poppler/bin/pdftoppm.exe",
            "paddle/bin/paddle_ocr_runner.exe", "paddle/models/det.bin", "paddle/runtime/core.dll",
        )
        for relative in required:
            path = root / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(relative.encode())
        spec = build_manifest(root, poppler_version="fixture-poppler", paddle_version="fixture-paddle",
                              max_files=20, max_bytes=100_000, max_component_files=20, max_component_bytes=100_000)
        manifest = root / "toolchain-manifest.json"
        manifest.write_text(json.dumps(spec, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        pin = _sha(manifest); (root / "toolchain-manifest.sha256").write_text(pin + "\n", encoding="ascii")
        bundle = TrustedToolchain(root, pin).verify_bundle()
        assert len(bundle.files) == len(required)
        # Sustitución posterior a la primera verificación debe ser fail-closed.
        (root / "paddle/models/det.bin").write_bytes(b"tampered")
        try:
            TrustedToolchain(root, pin).verify_bundle()
        except ToolchainError:
            pass
        else:
            raise AssertionError("No se detectó sustitución de asset")
        # El generador también debe rechazar reparse points, si el runner puede crearlos.
        link = root / "paddle/models/reparse.bin"
        try:
            os.symlink(root / "paddle/models/det.bin", link)
        except (OSError, NotImplementedError):
            print("WARN: Windows runner no permitió crear symlink; reparse se cubre en host de release")
        else:
            try:
                build_manifest(root, poppler_version="fixture-poppler", paddle_version="fixture-paddle",
                               max_files=30, max_bytes=100_000, max_component_files=30, max_component_bytes=100_000)
            except ValueError:
                pass
            else:
                raise AssertionError("No se rechazó reparse point")
    print("OK vendor release contract fixture (offline, pin no production)")
    return 0


if __name__ == "__main__": raise SystemExit(main())
