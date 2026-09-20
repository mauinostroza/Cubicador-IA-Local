from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from collections.abc import Callable
import json
import math
import os
import re
import secrets
import struct
import threading

from .pdf_text import PdfText
from .runtime import AuditLog, run_command
from .security import SecurityPolicy, SecurityViolation, ensure_within
from .toolchain import ToolchainError, resolve_poppler_for_launch, _reparse


class OcrError(RuntimeError):
    pass


def _strict_json_loads(data: str) -> object:
    def pairs(values: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in values:
            if key in result:
                raise ValueError("clave duplicada")
            result[key] = value
        return result
    def reject(_value: str) -> object:
        raise ValueError("número no finito")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=reject)


def _safe_worker_file(path: Path, workspace: Path, *, require_nonempty: bool = True) -> bool:
    try:
        root = Path(workspace).resolve(strict=True)
        candidate = path.resolve(strict=True)
        current = path
        while True:
            if _reparse(current): return False
            if current.resolve(strict=True) == root: break
            if not current.resolve(strict=True).is_relative_to(root) or current.parent == current: return False
            current = current.parent
        stat = candidate.stat()
        return candidate.is_file() and stat.st_nlink == 1 and (not require_nonempty or stat.st_size > 0)
    except OSError:
        return False


@dataclass(frozen=True, slots=True)
class OcrLine:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int
    confidence: float


@dataclass(frozen=True, slots=True)
class OcrPage:
    width: int
    height: int
    lines: tuple[OcrLine, ...]


class OcrProvider(Protocol):
    """Contrato mínimo: imagen local de entrada, líneas y coordenadas de salida."""

    def recognize(self, image_path: Path, *, policy: SecurityPolicy, workspace: Path,
                  audit: AuditLog | None = None, cancel: threading.Event | None = None) -> OcrPage: ...


def _tool(name: str, policy: SecurityPolicy) -> tuple[Path, Callable[[], Path]] | None:
    try: return resolve_poppler_for_launch(name, developer_mode=policy.developer_tools_enabled)
    except (ToolchainError, FileNotFoundError): return None


def render_pages(pdf_path: Path, page_count: int, policy: SecurityPolicy, workspace: Path,
                 audit: AuditLog | None = None, cancel: threading.Event | None = None) -> tuple[Path, ...]:
    """Renderiza un número acotado de páginas; nunca llama herramientas del PATH."""
    if page_count > policy.max_ocr_pages:
        raise OcrError(f"OCR rechazado: el PDF supera {policy.max_ocr_pages} páginas")
    resolved = _tool("pdftoppm", policy)
    if resolved is None:
        raise OcrError("No se encontró pdftoppm en la ubicación controlada de Poppler")
    executable, verifier = resolved
    prefix = ensure_within(workspace / "ocr-page", workspace)
    completed = run_command([
        str(executable), "-png", "-r", str(policy.ocr_search_dpi), "-scale-to", "4000", "-f", "1", "-l", str(page_count),
        "-singlefile" if page_count == 1 else "-forcenum", str(pdf_path), str(prefix),
        ], policy=policy, workspace=workspace, audit=audit, cancel=cancel, launch_verifier=verifier)
    if completed.returncode != 0:
        raise OcrError(completed.stderr.decode("utf-8", "replace").strip() or "pdftoppm falló")
    images = tuple(sorted(workspace.glob("ocr-page*.png")))
    if len(images) != page_count:
        raise OcrError("Poppler no generó el número esperado de páginas")
    total_pixels = 0
    for image in images:
        if _reparse(image) or not image.is_file():
            raise SecurityViolation("Poppler generó una salida enlazada o inválida")
        if image.stat().st_size > policy.max_ocr_image_bytes:
            raise SecurityViolation("Imagen OCR excede el límite permitido")
        width, height = _png_size(image)
        if width * height > policy.max_ocr_search_pixels:
            raise SecurityViolation("Imagen OCR excede el límite de píxeles")
        total_pixels += width * height
    if total_pixels > policy.max_ocr_total_pixels - policy.max_ocr_detail_pixels:
        raise SecurityViolation("Render de búsqueda agotó el presupuesto OCR del trabajo")
    return images


def _png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise OcrError("Poppler generó una imagen PNG inválida")
    return struct.unpack(">II", header[16:24])


def _render_crop(pdf_path: Path, page_number: int, box: tuple[int, int, int, int], search_size: tuple[int, int],
                 policy: SecurityPolicy, workspace: Path, audit: AuditLog | None,
                 cancel: threading.Event | None = None) -> Path:
    resolved = _tool("pdftoppm", policy)
    if resolved is None:
        raise OcrError("No se encontró pdftoppm en la ubicación controlada de Poppler")
    executable, verifier = resolved
    points_w, points_h = _page_size_points(pdf_path, page_number, policy, workspace, audit, cancel)
    x, y, width, height = _transform_box(box, search_size, (points_w, points_h), policy.ocr_detail_dpi)
    prefix = ensure_within(workspace / f"ocr-table-{page_number}", workspace)
    completed = run_command([str(executable), "-png", "-r", str(policy.ocr_detail_dpi),
                             "-f", str(page_number), "-l", str(page_number), "-singlefile",
                             "-x", str(x), "-y", str(y), "-W", str(width), "-H", str(height),
                             str(pdf_path), str(prefix)], policy=policy, workspace=workspace, audit=audit,
                             launch_verifier=verifier, cancel=cancel)
    output = prefix.with_suffix(".png")
    if completed.returncode != 0 or _reparse(output) or not output.is_file():
        raise OcrError("No se pudo renderizar el recorte de la tabla")
    crop_width, crop_height = _png_size(output)
    if output.stat().st_size > policy.max_ocr_image_bytes or crop_width * crop_height > policy.max_ocr_detail_pixels:
        raise SecurityViolation("Recorte OCR excede los límites")
    return output


def _transform_box(box: tuple[int, int, int, int], search_size: tuple[int, int],
                   page_points: tuple[float, float], detail_dpi: int) -> tuple[int, int, int, int]:
    scale_x = page_points[0] * detail_dpi / 72 / search_size[0]
    scale_y = page_points[1] * detail_dpi / 72 / search_size[1]
    return (round(box[0]*scale_x), round(box[1]*scale_y),
            round(box[2]*scale_x), round(box[3]*scale_y))


def _page_size_points(pdf_path: Path, page_number: int, policy: SecurityPolicy, workspace: Path,
                      audit: AuditLog | None, cancel: threading.Event | None = None) -> tuple[float, float]:
    resolved = _tool("pdfinfo", policy)
    if resolved is None:
        raise OcrError("No se encontró pdfinfo en la ubicación controlada de Poppler")
    executable, verifier = resolved
    completed = run_command([str(executable), "-f", str(page_number), "-l", str(page_number), str(pdf_path)],
                            policy=policy, workspace=workspace, audit=audit, cancel=cancel, launch_verifier=verifier)
    match = re.search(rb"(?m)^Page(?:\s+\d+)? size:\s*([0-9.]+)\s+x\s+([0-9.]+)\s+pts", completed.stdout)
    if completed.returncode != 0 or not match:
        raise OcrError("pdfinfo no informó dimensiones de página válidas")
    return float(match.group(1)), float(match.group(2))


_TITLE = re.compile(r"(?:tabla|cuadro)\s+de\s+cubicaci[oó]n", re.IGNORECASE)


def ocr_document(pdf_path: Path, page_count: int, provider: OcrProvider, policy: SecurityPolicy,
                 workspace: Path, audit: AuditLog | None = None,
                 cancel: threading.Event | None = None) -> PdfText:
    """OCR fail-closed: solo conserva páginas donde se identifica el título esperado."""
    images = render_pages(pdf_path, page_count, policy, workspace, audit, cancel)
    pages: list[str] = []
    found = False
    calls = 0
    total_pixels = 0
    evidence: dict[int, dict] = {}
    for page_number, image in enumerate(images, 1):
        if cancel and cancel.is_set():
            raise OcrError("Trabajo cancelado")
        if calls >= policy.max_ocr_pages:
            raise SecurityViolation("Presupuesto de llamadas OCR de búsqueda excedido")
        width, height = _png_size(image); total_pixels += width * height; calls += 1
        page = provider.recognize(image, policy=policy, workspace=workspace, audit=audit, cancel=cancel)
        _validate_page(page, policy.max_ocr_search_pixels)
        ordered = sorted(page.lines, key=lambda line: (line.y0, line.x0))
        title_rows = [line for line in ordered if _TITLE.search(" ".join(line.text.split()))]
        if not title_rows:
            pages.append("")
            continue
        found = True
        title = min(title_rows, key=lambda line: line.y0)
        crop_box = _grid_table_bounds(image, title, page)
        if crop_box is None:
            raise OcrError("No se confirmó el contorno raster de la tabla; requiere revisión")
        points_w, points_h = _page_size_points(pdf_path, page_number, policy, workspace, audit, cancel)
        predicted_detail = round(crop_box[2] * points_w * policy.ocr_detail_dpi / 72 / page.width) * round(crop_box[3] * points_h * policy.ocr_detail_dpi / 72 / page.height)
        if predicted_detail > policy.max_ocr_detail_pixels:
            raise SecurityViolation("Recorte detallado excedería el presupuesto de píxeles")
        crop = _render_crop(pdf_path, page_number, crop_box, (page.width, page.height),
                            policy, workspace, audit, cancel)
        if calls >= policy.max_ocr_calls:
            raise SecurityViolation("Presupuesto total de llamadas OCR excedido")
        cw, ch = _png_size(crop); total_pixels += cw * ch; calls += 1
        if total_pixels > policy.max_ocr_total_pixels:
            raise SecurityViolation("Presupuesto total de píxeles OCR excedido")
        cropped_page = provider.recognize(crop, policy=policy, workspace=workspace, audit=audit, cancel=cancel)
        _validate_page(cropped_page, policy.max_ocr_detail_pixels)
        selected = sorted((line for line in cropped_page.lines if line.confidence >= 0.35),
                          key=lambda line: (line.y0, line.x0))
        page_text, row_geometry = _geometric_content(selected)
        pages.append(page_text)
        bx, by, bw, bh = crop_box
        evidence[page_number] = {"bbox": crop_box, "confidence": min((line.confidence for line in selected), default=0),
                                 "dpi": policy.ocr_detail_dpi, "crop_sha256": _sha256(crop),
                                 "model_hashes": getattr(provider, "model_hashes", {}),
                                 "engine": getattr(provider, "engine", "ocr-local"),
                                 "engine_version": getattr(provider, "engine_version", "unknown"),
                                 "model_version": getattr(provider, "model_version", "unknown"),
                                 "rows": row_geometry,
                                 "detail_size": (cropped_page.width, cropped_page.height),
                                 "pdf_bbox_points": (bx*points_w/page.width, by*points_h/page.height,
                                                     (bx+bw)*points_w/page.width, (by+bh)*points_h/page.height)}
        pages.extend("" for _ in range(page_number, page_count))
        break
    if not found:
        raise OcrError("OCR no encontró 'Tabla de cubicación' ni 'Cuadro de cubicación'")
    return PdfText(tuple(pages), ocr_evidence=evidence)


def _grid_table_bounds(image_path: Path, title: OcrLine, page: OcrPage) -> tuple[int, int, int, int] | None:
    """Busca la secuencia contigua de bordes de la grilla que contiene el título."""
    try:
        from PIL import Image
        with Image.open(image_path) as source:
            gray = source.convert("L")
            if gray.size != (page.width, page.height): return None
            tw, th = title.x1-title.x0, title.y1-title.y0
            x0, x1 = max(0, title.x0-2*tw), min(page.width, title.x1+2*tw)
            y0, y1 = max(0, title.y0-2*th), min(page.height, title.y1+30*th)
            pixels = gray.load()
            center = (title.x0 + title.x1) // 2
            raw_spans: list[tuple[int, int, int]] = []
            minimum_run = max(20, round(tw * .70))
            for y in range(y0, y1):
                start = None
                for x in range(x0, x1 + 1):
                    dark = x < x1 and pixels[x, y] < 100
                    if dark and start is None:
                        start = x
                    elif not dark and start is not None:
                        if x-start >= minimum_run and start <= center <= x-1:
                            raw_spans.append((y, start, x-1))
                        start = None
            groups: list[list[tuple[int, int, int]]] = []
            for span in raw_spans:
                if groups and span[0] <= groups[-1][-1][0] + 1:
                    groups[-1].append(span)
                else:
                    groups.append([span])
            lines = [(round(sum(v[0] for v in group)/len(group)),
                      round(sum(v[1] for v in group)/len(group)),
                      round(sum(v[2] for v in group)/len(group))) for group in groups]
    except (OSError, ValueError):
        return None
    top_candidates = [line for line in lines if line[0] <= title.y0 + max(3, th//4)]
    if not top_candidates:
        return None
    top_line = max(top_candidates, key=lambda value: value[0])
    top, left, right = top_line
    base_width = right-left
    sequence = [top_line]
    max_gap = max(18, round(th * 2.5))
    for line in (value for value in lines if value[0] > top):
        y, candidate_left, candidate_right = line
        if y-sequence[-1][0] > max_gap:
            break
        overlap = max(0, min(right, candidate_right)-max(left, candidate_left))
        candidate_width = candidate_right-candidate_left
        if overlap >= .80*base_width and .75*base_width <= candidate_width <= 1.25*base_width:
            sequence.append(line)
    if len(sequence) < 4:
        return None
    bottom = sequence[-1][0]
    left = round(sum(v[1] for v in sequence)/len(sequence))
    right = round(sum(v[2] for v in sequence)/len(sequence))
    if right-left < tw or bottom-top < 2*th or bottom >= y1-2:
        return None
    # Los bordes laterales deben ser líneas reales a lo largo de la tabla.
    vertical_ratio = lambda x: sum(pixels[x, y] < 100 for y in range(top, bottom+1)) / max(1, bottom-top+1)
    left_ok = any(vertical_ratio(x) >= .55 for x in range(max(x0,left-4), min(x1,left+5)))
    right_ok = any(vertical_ratio(x) >= .55 for x in range(max(x0,right-4), min(x1,right+5)))
    if not (left_ok and right_ok):
        return None
    pad = max(4, th//3)
    return max(0,left-pad), max(0,top-pad), min(page.width,right+pad)-max(0,left-pad), min(page.height,bottom+pad)-max(0,top-pad)


def _validate_page(page: OcrPage, pixel_limit: int) -> None:
    if page.width <= 0 or page.height <= 0 or page.width * page.height > pixel_limit or len(page.lines) > 10_000:
        raise SecurityViolation("Salida OCR fuera de límites")
    for line in page.lines:
        if not line.text.strip() or len(line.text) > 2000 or not 0 <= line.confidence <= 1:
            raise OcrError("Línea OCR inválida")
        if not (0 <= line.x0 < line.x1 <= page.width and 0 <= line.y0 < line.y1 <= page.height):
            raise OcrError("BBox OCR inválido")


def _geometric_content(lines: list[OcrLine]) -> tuple[str, list[dict]]:
    rows: list[list[OcrLine]] = []
    for line in lines:
        target = next((row for row in rows if abs(sum(v.y0 for v in row) / len(row) - line.y0) <= max(5, (line.y1-line.y0)//2)), None)
        (target if target is not None else rows.append([]) or rows[-1]).append(line)
    output, geometry = [], []
    for row in rows:
        row.sort(key=lambda value: value.x0)
        chunks, cursor = [], 0
        for line in row:
            spaces = max(1, round((line.x0 - cursor) / max(5, (line.y1-line.y0) * .45)))
            chunks.append(" " * spaces + " ".join(line.text.split())); cursor = line.x1
        output.append("".join(chunks).lstrip())
        geometry.append({"bbox": (min(v.x0 for v in row), min(v.y0 for v in row), max(v.x1 for v in row), max(v.y1 for v in row)),
                         "confidence": min(v.confidence for v in row),
                         "cells": [{"text": v.text, "bbox": (v.x0,v.y0,v.x1,v.y1), "confidence": v.confidence} for v in row]})
    return "\n".join(output), geometry


def _geometric_text(lines: list[OcrLine]) -> str:
    return _geometric_content(lines)[0]


def _sha256(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""): digest.update(chunk)
    return digest.hexdigest()


class PaddleOcrProvider:
    """Adaptador offline para un runner PaddleOCR previamente empaquetado.

    El runner y los modelos deben existir bajo ``vendor``. No se descarga nada ni se
    importa Paddle dentro del proceso principal.
    """

    def __init__(self):
        from .toolchain import TrustedToolchain
        self.toolchain = TrustedToolchain(Path(__file__).resolve().parents[3] / "vendor")
        try:
            self.runner = self.toolchain.verify_and_resolve("paddle.runner")
            self.model_root = (self.toolchain.vendor_root / "paddle" / "models").resolve(strict=True)
        except Exception as exc:
            raise OcrError("Toolchain PaddleOCR no disponible") from exc
        self.engine, self.engine_version, self.model_version = "PaddleOCR-minimal", "pinned", "pinned"
        self.model_hashes = {"toolchain_manifest": self.toolchain.trusted_manifest_sha256}

    def recognize(self, image_path: Path, *, policy: SecurityPolicy, workspace: Path,
                  audit: AuditLog | None = None, cancel: threading.Event | None = None) -> OcrPage:
        # Revalida inventario completo y prepara una segunda atestación que
        # run_command ejecuta justo antes de Popen/CreateProcessW. Esto cubre
        # runner, DLL/runtime y todos los modelos del inventario.
        self.runner = self.toolchain.verify_and_resolve("paddle.runner")
        verifier = self.toolchain.launch_verifier("paddle.runner", self.runner)
        self.model_root = ensure_within(self.model_root, self.toolchain.vendor_root)
        image = ensure_within(image_path, workspace)
        job_id = secrets.token_hex(16)
        output = ensure_within(workspace / f"{image.stem}.{job_id}.ocr.json", workspace)
        request = ensure_within(workspace / f"{image.stem}.{job_id}.ocr-request.json", workspace)
        if request.exists() or output.exists():
            raise SecurityViolation("Archivos reservados OCR ya existen")
        payload = {
            "schema": 1, "operation": "recognize", "job_id": job_id,
            "image": str(image), "output": str(output),
            "limits": {"max_image_bytes": policy.max_ocr_image_bytes,
                       "max_pixels": max(policy.max_ocr_search_pixels, policy.max_ocr_detail_pixels),
                       "max_response_bytes": policy.max_response_bytes,
                       "max_lines": 10_000, "max_text_chars": 500_000},
        }
        encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"): flags |= os.O_BINARY
        if hasattr(os, "O_NOFOLLOW"): flags |= os.O_NOFOLLOW
        fd = os.open(request, flags, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        completed = run_command([str(self.runner), "--request", str(request), "--workspace", str(Path(workspace).resolve())],
                                policy=policy, workspace=workspace, audit=audit,
                                launch_verifier=verifier, cancel=cancel,
                                timeout=policy.model_timeout_seconds)
        if completed.stdout or completed.stderr:
            raise SecurityViolation("El runner OCR escribió en canales no autorizados")
        if completed.returncode != 0 or not _safe_worker_file(output, Path(workspace)):
            raise OcrError("PaddleOCR local falló o no generó salida")
        if output.stat().st_size <= 0 or output.stat().st_size > policy.max_response_bytes:
            raise SecurityViolation("Respuesta OCR excede el límite")
        try:
            raw = _strict_json_loads(output.read_text("utf-8"))
            if (not isinstance(raw, dict) or set(raw) != {"schema", "ok", "job_id", "width", "height", "lines"}
                    or type(raw.get("schema")) is not int or raw.get("schema") != 1
                    or raw.get("ok") is not True or type(raw.get("job_id")) is not str
                    or raw.get("job_id") != job_id or type(raw.get("width")) is not int
                    or type(raw.get("height")) is not int or not isinstance(raw.get("lines"), list)):
                raise ValueError("esquema inválido")
            if (raw["width"], raw["height"]) != _png_size(image):
                raise ValueError("dimensiones no coinciden")
            lines_list: list[OcrLine] = []
            for value in raw["lines"]:
                if (not isinstance(value, dict)
                        or set(value) != {"text", "x0", "y0", "x1", "y1", "confidence"}
                        or type(value["text"]) is not str
                        or any(type(value[key]) is not int for key in ("x0", "y0", "x1", "y1"))
                        or type(value["confidence"]) not in (int, float)
                        or not math.isfinite(value["confidence"])):
                    raise ValueError("línea inválida")
                lines_list.append(OcrLine(value["text"], value["x0"], value["y0"],
                                          value["x1"], value["y1"], float(value["confidence"])))
            page = OcrPage(raw["width"], raw["height"], tuple(lines_list))
            _validate_page(page, max(policy.max_ocr_search_pixels, policy.max_ocr_detail_pixels))
            return page
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise OcrError("Salida PaddleOCR inválida") from exc
