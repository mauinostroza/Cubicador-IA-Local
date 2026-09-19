"""Valida el contrato pendiente de OCR sin descargar ni ejecutar activos.

El comando debe fallar mientras el lock no tenga pins de release. Eso evita
que un build convierta por accidente el contrato documental en una activación.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def validate(lock_path: Path) -> list[str]:
    spec = json.loads(lock_path.read_text("utf-8"))
    errors: list[str] = []
    if spec.get("schema_version") != 2:
        errors.append("schema_version debe ser 2")
    if spec.get("release_enabled") is not False:
        errors.append("release_enabled debe permanecer false")
    gates = spec.get("feature_gates", {})
    if any(gates.get(name) is not False for name in ("pdfium", "paddle_ocr", "poppler_payload")):
        errors.append("todos los feature gates deben estar cerrados")
    artifacts = spec.get("artifacts", {})
    for name, artifact in artifacts.items():
        if not isinstance(artifact, dict):
            errors.append(f"asset inválido: {name}")
            continue
        for field in ("version", "url", "sha256", "size_bytes"):
            if artifact.get(field) is not None:
                errors.append(f"el contrato pendiente no debe fijar {name}.{field} sin release aprobado")
        if name == "poppler_legacy_dev_only" and artifact.get("status") != "never_in_release_payload":
            errors.append("Poppler debe estar marcado como legacy/dev únicamente")
    provenance = spec.get("provenance", {})
    root = lock_path.parent
    for field in ("spdx_inputs", "sbom_template", "notices"):
        value = provenance.get(field)
        if not isinstance(value, str) or not (root / value).is_file():
            errors.append(f"falta input de provenance: {field}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=Path(__file__).parents[1] / "ocr-release.lock.json")
    args = parser.parse_args()
    try:
        errors = validate(args.lock.resolve(strict=True))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL closed: {exc}")
        return 2
    if errors:
        print("FAIL closed:")
        for error in errors:
            print(f"- {error}")
        return 2
    print("OK: contrato pendiente, sin payload activo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
