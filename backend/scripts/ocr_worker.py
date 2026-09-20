"""Prototipo de runner OCR mínimo, offline y fail-closed.

El proceso no importa PaddleOCR/PaddleX, no descarga activos y no acepta URLs.
Este script no forma parte del payload de release actual ni activa OCR.
El backend de inferencia se inyecta solamente en pruebas; el entrypoint de
producción permanece bloqueado hasta que el runtime y los modelos formen parte
del manifiesto confiable.
"""
from __future__ import annotations

import json
import math
import os
import re
import struct
import sys
from pathlib import Path
from typing import Callable


SCHEMA = 1
MAX_REQUEST_BYTES = 64 * 1024
MAX_LINES_HARD = 10_000


class WorkerError(RuntimeError):
    pass


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise WorkerError("JSON OCR contiene claves duplicadas")
        result[key] = value
    return result


def _strict_json(data: str) -> object:
    def reject(_value: str) -> object:
        raise WorkerError("Número JSON no finito")
    return json.loads(data, object_pairs_hook=_pairs, parse_constant=reject)


def _reparse(path: Path) -> bool:
    if path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)()):
        return True
    try:
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        return False


def _safe_regular(path: Path) -> bool:
    try:
        return path.is_file() and not _reparse(path) and path.stat().st_nlink == 1
    except OSError:
        return False


def _clean_ancestors(path: Path, root: Path) -> bool:
    current = path
    while True:
        if _reparse(current):
            return False
        if current == root:
            return True
        if not current.is_relative_to(root) or current.parent == current:
            return False
        current = current.parent


def _inside(value: object, workspace: Path, *, must_exist: bool) -> Path:
    if type(value) is not str or "\x00" in value:
        raise WorkerError("Ruta OCR inválida")
    candidate = Path(value)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise WorkerError("Ruta OCR inválida")
    root = workspace.resolve(strict=True)
    resolved = candidate.resolve(strict=must_exist)
    check_from = resolved if resolved.exists() else resolved.parent
    if not resolved.is_relative_to(root) or not _clean_ancestors(check_from, root):
        raise WorkerError("Ruta OCR fuera del workspace")
    return resolved


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as source:
        header = source.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise WorkerError("Entrada OCR no es PNG")
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        raise WorkerError("Dimensiones PNG inválidas")
    return width, height


def _open_png(path: Path, width: int, height: int) -> None:
    """Decodifica completamente el PNG después de aplicar el límite de píxeles."""
    try:
        from PIL import Image
        with Image.open(path) as source:
            if source.format != "PNG" or source.size != (width, height):
                raise WorkerError("Contenido PNG inconsistente")
            source.load()
    except WorkerError:
        raise
    except (OSError, ValueError) as exc:
        raise WorkerError("PNG corrupto o no decodificable") from exc


def _limits(raw: object) -> dict[str, int]:
    keys = {"max_image_bytes", "max_pixels", "max_response_bytes", "max_lines", "max_text_chars"}
    if not isinstance(raw, dict) or set(raw) != keys:
        raise WorkerError("Límites OCR inválidos")
    if any(type(raw[key]) is not int or raw[key] <= 0 for key in keys):
        raise WorkerError("Límites OCR inválidos")
    if raw["max_lines"] > MAX_LINES_HARD or raw["max_response_bytes"] > 2 * 1024 * 1024:
        raise WorkerError("Límites OCR exceden el máximo del runner")
    return {key: int(raw[key]) for key in keys}


def _validated_response(raw: object, width: int, height: int, limits: dict[str, int]) -> dict:
    if not isinstance(raw, dict) or set(raw) != {"lines"} or not isinstance(raw["lines"], list):
        raise WorkerError("Respuesta del backend OCR inválida")
    if len(raw["lines"]) > limits["max_lines"]:
        raise WorkerError("Demasiadas líneas OCR")
    clean: list[dict] = []
    text_total = 0
    for value in raw["lines"]:
        if not isinstance(value, dict) or set(value) != {"text", "bbox", "confidence"}:
            raise WorkerError("Línea OCR inválida")
        text = value["text"]
        box = value["bbox"]
        confidence = value["confidence"]
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise WorkerError("Texto OCR inválido")
        text_total += len(text)
        if text_total > limits["max_text_chars"]:
            raise WorkerError("Texto OCR excede la cuota")
        if (not isinstance(box, list) or len(box) != 4
                or any(type(part) is not int for part in box)):
            raise WorkerError("BBox OCR inválido")
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
            raise WorkerError("BBox OCR fuera de imagen")
        if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise WorkerError("Confianza OCR inválida")
        clean.append({"text": text, "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                      "confidence": float(confidence)})
    return {"schema": SCHEMA, "ok": True, "width": width, "height": height, "lines": clean}


def _unavailable_backend(_image: Path, _width: int, _height: int) -> dict:
    raise WorkerError("Backend OCR no activado: faltan runtime/modelos fijados")


def _write_atomic(path: Path, payload: bytes, job_id: str) -> None:
    temporary = path.with_name(f".{path.name}.{job_id}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        if path.exists():
            raise WorkerError("Salida OCR reservada ya existe")
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
        except OSError:
            pass
    except Exception:
        try: temporary.unlink()
        except OSError: pass
        raise


def run_request(request_path: Path, workspace: Path,
                backend: Callable[[Path, int, int], dict] | None = None) -> None:
    request = _inside(str(request_path), workspace, must_exist=True)
    if request.stat().st_size <= 0 or request.stat().st_size > MAX_REQUEST_BYTES:
        raise WorkerError("Solicitud OCR fuera de cuota")
    if not _safe_regular(request):
        raise WorkerError("Solicitud OCR enlazada o compartida")
    raw = _strict_json(request.read_text("utf-8"))
    expected = {"schema", "operation", "job_id", "image", "output", "limits"}
    if (not isinstance(raw, dict) or set(raw) != expected or type(raw.get("schema")) is not int
            or raw.get("schema") != SCHEMA or type(raw.get("operation")) is not str
            or raw.get("operation") != "recognize" or type(raw.get("job_id")) is not str
            or not re.fullmatch(r"[0-9a-f]{32}", raw["job_id"])):
        raise WorkerError("Esquema OCR no autorizado")
    limits = _limits(raw["limits"])
    image = _inside(raw["image"], workspace, must_exist=True)
    output = _inside(raw["output"], workspace, must_exist=False)
    if image.suffix.lower() != ".png" or not _safe_regular(image) or image.stat().st_size > limits["max_image_bytes"]:
        raise WorkerError("Imagen OCR inválida o fuera de cuota")
    if output.exists():
        raise WorkerError("Salida OCR reservada ya existe")
    width, height = _png_dimensions(image)
    if width * height > limits["max_pixels"]:
        raise WorkerError("Imagen OCR excede la cuota de píxeles")
    _open_png(image, width, height)
    response = _validated_response((backend or _unavailable_backend)(image, width, height), width, height, limits)
    response["job_id"] = raw["job_id"]
    encoded = (json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > limits["max_response_bytes"]:
        raise WorkerError("Respuesta OCR excede la cuota")
    _write_atomic(output, encoded, raw["job_id"])


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 4 or args[0] != "--request" or args[2] != "--workspace":
        return 2
    try:
        workspace = Path(args[3])
        run_request(Path(args[1]), workspace)
        return 0
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError, WorkerError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
