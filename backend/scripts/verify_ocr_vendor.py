from __future__ import annotations

import argparse
from pathlib import Path
from cubicador.toolchain import ToolchainError, TrustedToolchain


def main() -> int:
    parser = argparse.ArgumentParser(description="Verifica offline los assets OCR antes de activarlos")
    parser.add_argument("--vendor-root", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        chain = TrustedToolchain(args.vendor_root, args.manifest_sha256)
        # Una sola lectura/hash del cierre completo. Resolver cada herramienta
        # con verify_and_resolve repetiría el coste y no añade seguridad dentro
        # de esta operación; el atestado tardío de lanzamiento sí vuelve a
        # verificar justo antes de crear cada proceso.
        bundle = chain.verify_bundle()
        for name in ("poppler.pdftotext", "poppler.pdfinfo", "poppler.pdftoppm", "paddle.runner"):
            if name not in bundle.tools:
                raise ToolchainError(f"Herramienta ausente: {name}")
        print("Versiones:", ", ".join(f"{k}={v}" for k, v in sorted(bundle.versions.items())))
    except ToolchainError as exc:
        print(exc); return 2
    print("OK toolchain completo")
    return 0


if __name__ == "__main__": raise SystemExit(main())
