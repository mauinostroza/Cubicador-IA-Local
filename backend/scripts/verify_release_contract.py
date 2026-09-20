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
    verified_inputs = set(spec.get("build_inputs", []))
    if not isinstance(spec.get("build_inputs", []), list) or not all(isinstance(name, str) for name in spec.get("build_inputs", [])):
        errors.append("build_inputs debe ser una lista de nombres")
    for name, artifact in artifacts.items():
        if not isinstance(artifact, dict):
            errors.append(f"asset inválido: {name}")
            continue
        status = artifact.get("status")
        if status == "verified_upstream_pypi":
            for field in ("version", "filename", "url", "sha256", "size_bytes", "source_record"):
                if not artifact.get(field):
                    errors.append(f"asset verificado incompleto: {name}.{field}")
            if not isinstance(artifact.get("size_bytes"), int) or artifact["size_bytes"] <= 0:
                errors.append(f"tamaño inválido para asset verificado: {name}")
            digest = artifact.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                errors.append(f"SHA-256 inválido para asset verificado: {name}")
            if name not in verified_inputs:
                errors.append(f"asset verificado no declarado en build_inputs: {name}")
        elif status == "verified_upstream_hf":
            for field in ("version", "filename", "url", "sha256", "size_bytes", "source_commit"):
                if not artifact.get(field):
                    errors.append(f"asset HF verificado incompleto: {name}.{field}")
            if name in verified_inputs:
                errors.append(f"asset HF no pertenece al staging PyPI build_inputs: {name}")
            if artifact.get("version") != artifact.get("source_commit"):
                errors.append(f"commit/version HF no coincide: {name}")
            if (not isinstance(artifact.get("url"), str)
                    or not artifact["url"].startswith("https://huggingface.co/PaddlePaddle/")
                    or f"/resolve/{artifact.get('source_commit')}/" not in artifact["url"]):
                errors.append(f"URL HF no está fijada al commit: {name}")
            digest = artifact.get("sha256")
            if (not isinstance(artifact.get("size_bytes"), int) or artifact["size_bytes"] <= 0
                    or not isinstance(digest, str) or len(digest) != 64
                    or any(char not in "0123456789abcdef" for char in digest)):
                errors.append(f"pin HF inválido: {name}")
        elif str(status).startswith("blocked_"):
            for field in ("version", "filename", "url", "sha256", "size_bytes"):
                if artifact.get(field) is not None:
                    errors.append(f"asset bloqueado no puede fijar {name}.{field}")
            if not isinstance(artifact.get("blocked_reason"), str) or not artifact["blocked_reason"].strip():
                errors.append(f"asset bloqueado sin motivo: {name}")
        elif status == "excluded_from_release_reference":
            if name in verified_inputs:
                errors.append(f"referencia excluida no puede ser build input: {name}")
            if not isinstance(artifact.get("excluded_reason"), str) or not artifact["excluded_reason"].strip():
                errors.append(f"referencia excluida sin motivo: {name}")
            digest = artifact.get("sha256")
            if not isinstance(digest, str) or len(digest) != 64:
                errors.append(f"referencia excluida sin SHA-256 informativo: {name}")
        else:
            for field in ("version", "url", "sha256", "size_bytes"):
                if artifact.get(field) is not None:
                    errors.append(f"estado de asset no reconocido para {name}.{field}")
        if name == "poppler_legacy_dev_only" and artifact.get("status") != "never_in_release_payload":
            errors.append("Poppler debe estar marcado como legacy/dev únicamente")
    provenance = spec.get("provenance", {})
    root = lock_path.parent
    for field in ("spdx_inputs", "sbom_template", "notices"):
        value = provenance.get(field)
        if not isinstance(value, str) or not (root / value).is_file():
            errors.append(f"falta input de provenance: {field}")
    sbom_path = root / provenance.get("sbom_template", "")
    if sbom_path.is_file():
        try:
            sbom = json.loads(sbom_path.read_text("utf-8"))
            packages = {item.get("packageFileName"): item for item in sbom.get("packages", []) if isinstance(item, dict)}
            for name, artifact in artifacts.items():
                if artifact.get("status") not in {"verified_upstream_pypi", "excluded_from_release_reference"}:
                    continue
                package = packages.get(artifact.get("filename"))
                if not package:
                    errors.append(f"asset sin registro SBOM de staging: {name}")
                    continue
                checksums = {item.get("algorithm"): item.get("checksumValue") for item in package.get("checksums", [])}
                if package.get("downloadLocation") != artifact.get("url") or checksums.get("SHA256") != artifact.get("sha256"):
                    errors.append(f"SBOM no coincide con lock: {name}")
                if artifact.get("status") == "excluded_from_release_reference" and "REFERENCE ONLY" not in package.get("comment", ""):
                    errors.append(f"SBOM no marca referencia excluida: {name}")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"SBOM de staging inválido: {exc}")
    for name in verified_inputs:
        if name not in artifacts or artifacts[name].get("status") != "verified_upstream_pypi":
            errors.append(f"build input no verificable: {name}")
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
