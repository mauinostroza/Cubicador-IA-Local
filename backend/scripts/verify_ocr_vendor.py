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
        for name in ("poppler.pdftotext", "poppler.pdfinfo", "poppler.pdftoppm", "paddle.runner"):
            chain.verify_and_resolve(name)
    except ToolchainError as exc:
        print(exc); return 2
    print("OK toolchain completo")
    return 0


if __name__ == "__main__": raise SystemExit(main())
