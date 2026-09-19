"""Proceso hijo PDFium, deliberadamente separado de la API y del pipeline.

El padre entrega dos JSON en un workspace privado. Este proceso solo puede
abrir el PDF y escribir la respuesta dentro de ese workspace; no acepta URLs,
comandos, plugins ni variables de entorno para activar funciones adicionales.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


if __package__ in {None, ""}:
    _backend = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(_backend / "src"))

from cubicador.pdf_backend import PdfBackendError, PdfiumBackend
from cubicador.security import SecurityPolicy, SecurityViolation


SCHEMA = 1
MAX_REQUEST_BYTES = 64 * 1024


def _inside(path: str, workspace: Path, *, must_exist: bool = True) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise SecurityViolation("Ruta PDFium inválida")
    resolved = candidate.resolve(strict=must_exist)
    root = workspace.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise SecurityViolation("Ruta PDFium fuera del workspace")
    return resolved


def _policy(raw: dict) -> SecurityPolicy:
    allowed = {
        "max_pdf_bytes", "max_pages", "max_ocr_detail_pixels", "max_ocr_image_bytes",
        "max_response_bytes",
    }
    if set(raw) != allowed or any(not isinstance(raw[key], int) for key in raw):
        raise SecurityViolation("Política PDFium inválida")
    values = {key: raw[key] for key in allowed if key in raw}
    values.update(pdfium_enabled=True, pdfium_worker_enabled=True)
    return SecurityPolicy(**values)


def _span(value: object) -> dict:
    return {
        "text": value.text,
        "bbox": [float(part) for part in value.bbox],
        "cells": [{"text": cell.text, "bbox": [float(part) for part in cell.bbox]} for cell in value.cells],
    }


def _run(request_path: Path, output_path: Path, workspace: Path) -> None:
    if request_path.stat().st_size > MAX_REQUEST_BYTES:
        raise SecurityViolation("Solicitud PDFium demasiado grande")
    raw = json.loads(request_path.read_text("utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"schema", "operation", "pdf", "output", "policy"} or raw.get("schema") != SCHEMA or raw.get("operation") != "extract":
        raise SecurityViolation("Esquema PDFium no autorizado")
    if not all(isinstance(raw.get(key), str) for key in ("pdf", "output")) or not isinstance(raw.get("policy"), dict):
        raise SecurityViolation("Tipos de solicitud PDFium inválidos")
    pdf = _inside(str(raw["pdf"]), workspace)
    if pdf.suffix.lower() != ".pdf" or not pdf.is_file():
        raise SecurityViolation("Entrada PDFium inválida")
    output = _inside(str(raw["output"]), workspace, must_exist=False)
    if output.exists() and output.is_symlink():
        raise SecurityViolation("Salida PDFium enlazada")
    policy = _policy(raw.get("policy") if isinstance(raw.get("policy"), dict) else {})
    backend = PdfiumBackend.from_policy(policy)
    count = backend.page_count(pdf, policy=policy)
    pages = []
    for number in range(1, count + 1):
        page = backend.extract_page(pdf, number, policy=policy)
        pages.append({
            "page_number": page.page_number,
            "width_points": page.width_points,
            "height_points": page.height_points,
            "rotation": page.rotation,
            "bbox": list(page.bbox),
            "text": page.text,
            "lines": [_span(line) for line in page.lines],
        })
    response = {"schema": SCHEMA, "ok": True, "engine": "PDFium", "pages": pages}
    encoded = (json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > policy.max_response_bytes:
        raise SecurityViolation("Respuesta PDFium excede la cuota")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(output, flags, 0o600)
    except FileExistsError as exc:
        raise SecurityViolation("Salida PDFium ya existe") from exc
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        try:
            output.unlink()
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 4 or argv[0] != "--request" or argv[2] != "--workspace":
        return 2
    request = Path(argv[1])
    workspace = Path(argv[3])
    try:
        request = _inside(str(request), workspace)
        raw = json.loads(request.read_text("utf-8"))
        output = Path(str(raw["output"]))
        _run(request, output, workspace)
        return 0
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, PdfBackendError, SecurityViolation):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
