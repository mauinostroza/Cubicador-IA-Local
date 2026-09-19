from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import math
import re
import shutil
import sys
import threading
from .security import DEFAULT_SECURITY_POLICY, SecurityPolicy
from .runtime import AuditLog, JobWorkspace, atomic_write_bytes, run_command
from .toolchain import ToolchainError, resolve_poppler_for_launch


class PdfTextError(RuntimeError):
    pass


class NoTextPdfError(PdfTextError):
    pass


def get_page_count(pdf_path: str | Path, policy: SecurityPolicy, workspace: Path,
                   audit: AuditLog | None = None, cancel: threading.Event | None = None) -> int:
    path = policy.validate_pdf(pdf_path)
    try: executable, verifier = resolve_poppler_for_launch("pdfinfo", developer_mode=policy.developer_tools_enabled)
    except (ToolchainError, FileNotFoundError):
        raise PdfTextError("No se encontró pdfinfo en la ubicación controlada de Poppler")
    completed = run_command([str(executable), str(path)], policy=policy, workspace=workspace, audit=audit, cancel=cancel,
                             launch_verifier=verifier)
    if completed.returncode != 0:
        raise PdfTextError("pdfinfo no pudo inspeccionar el PDF")
    import re
    match = re.search(rb"(?m)^Pages:\s*(\d+)\s*$", completed.stdout)
    if not match:
        raise PdfTextError("pdfinfo no informó una cantidad de páginas válida")
    count = int(match.group(1))
    if not 1 <= count <= policy.max_pages:
        raise PdfTextError(f"Cantidad de páginas fuera del límite ({policy.max_pages})")
    return count


@dataclass(frozen=True)
class PdfText:
    pages: tuple[str, ...]
    raw: str = ""
    ocr_evidence: dict[int, dict] | None = None
    # Geometría opcional del backend PDFium; Poppler legacy deja este campo
    # vacío para conservar compatibilidad con el extractor textual existente.
    layout: tuple[tuple[object, ...], ...] = ()

    @property
    def full_text(self) -> str:
        return self.raw or "\f".join(self.pages)


def extract_layout_text(pdf_path: str | Path, policy: SecurityPolicy, workspace: Path,
                        audit: AuditLog | None = None, cancel: threading.Event | None = None) -> PdfText:
    try:
        path = policy.validate_pdf(pdf_path)
    except (ValueError, OSError) as exc:
        raise PdfTextError(str(exc)) from exc
    try: executable_path, verifier = resolve_poppler_for_launch("pdftotext", developer_mode=policy.developer_tools_enabled)
    except (ToolchainError, FileNotFoundError):
        raise PdfTextError("No se encontró pdftotext en la ubicación controlada de Poppler")
    completed = run_command(
        [str(executable_path), "-layout", "-enc", "UTF-8", str(path), "-"],
        policy=policy, workspace=workspace, audit=audit, cancel=cancel, launch_verifier=verifier,
    )
    if completed.returncode != 0:
        raise PdfTextError(completed.stderr.decode("utf-8", "replace").strip() or "pdftotext falló")
    # Solo se quita el separador final agregado por Poppler; las páginas vacías
    # intermedias se conservan para no desplazar la numeración del plano.
    stdout = completed.stdout.decode("utf-8", "replace")
    raw_pages = stdout.split("\f")
    if raw_pages and raw_pages[-1] == "":
        raw_pages.pop()
    pages = tuple(raw_pages)
    if not pages or len(pages) > policy.max_pages:
        raise PdfTextError(f"Cantidad de páginas fuera del límite ({policy.max_pages})")
    if not any(page.strip() for page in pages):
        raise NoTextPdfError("El PDF no contiene texto legible")
    return PdfText(pages=pages, raw=stdout)


def extract_layout_text_pdfium(pdf_path: str | Path, policy: SecurityPolicy,
                               workspace: Path | None = None,
                               audit: AuditLog | None = None,
                               cancel: threading.Event | None = None) -> PdfText:
    """Extrae PDFium en un proceso hijo acotado y supervisado.

    El proceso API nunca importa pypdfium2: solo serializa una solicitud y
    valida una respuesta JSON. El worker recibe una copia del PDF dentro del
    workspace para que su árbol de proceso no pueda leer la ruta original.
    """
    if not policy.pdfium_enabled or not policy.pdfium_worker_enabled:
        raise PdfTextError("PDFium está desactivado o fuera del worker autorizado")
    source = policy.validate_pdf(pdf_path)
    managed = workspace is None
    holder = JobWorkspace(policy) if managed else None
    try:
        if holder is not None:
            work = holder.__enter__()
        else:
            work = Path(workspace).resolve(strict=True)
            if not work.is_dir() or work.is_symlink():
                raise PdfTextError("Workspace PDFium inválido")
        input_path = work / "pdfium-input.pdf"
        request_path = work / "pdfium-request.json"
        output_path = work / "pdfium-response.json"
        for reserved in (input_path, request_path, output_path):
            if reserved.exists() or reserved.is_symlink():
                raise PdfTextError("Workspace PDFium contiene una salida reservada")
        shutil.copyfile(source, input_path)
        request = {
            "schema": 1, "operation": "extract", "pdf": str(input_path), "output": str(output_path),
            "policy": {
                "max_pdf_bytes": policy.max_pdf_bytes,
                "max_pages": policy.max_pages,
                "max_ocr_detail_pixels": policy.max_ocr_detail_pixels,
                "max_ocr_image_bytes": policy.max_ocr_image_bytes,
                "max_response_bytes": policy.max_response_bytes,
            },
        }
        encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8")
        atomic_write_bytes(request_path, encoded, policy)
        worker = Path(__file__).resolve().parents[2] / "scripts" / "pdfium_worker.py"
        if not worker.is_file():
            raise PdfTextError("Worker PDFium no está instalado")
        completed = run_command(
            [sys.executable, str(worker), "--request", str(request_path), "--workspace", str(work)],
            policy=policy, workspace=work, audit=audit, cancel=cancel,
        )
        if completed.returncode != 0 or not output_path.is_file() or output_path.is_symlink():
            raise PdfTextError("Worker PDFium no pudo completar la extracción")
        if output_path.stat().st_size > policy.max_response_bytes:
            raise PdfTextError("Respuesta PDFium excede el límite permitido")
        try:
            payload = json.loads(output_path.read_text("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PdfTextError("Respuesta PDFium inválida") from exc
        return _pdfium_response(payload, policy)
    except (OSError, ValueError) as exc:
        raise PdfTextError(str(exc)) from exc
    finally:
        if holder is not None:
            holder.__exit__(None, None, None)


def _pdfium_response(payload: object, policy: SecurityPolicy) -> PdfText:
    """Valida estrictamente el contrato worker antes de exponer geometría."""
    from .pdf_backend import PdfTextCell, PdfTextSpan
    if (not isinstance(payload, dict) or set(payload) != {"schema", "ok", "engine", "pages"}
            or payload.get("schema") != 1 or payload.get("ok") is not True
            or payload.get("engine") != "PDFium"):
        raise PdfTextError("Esquema PDFium no autorizado")
    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list) or not 1 <= len(raw_pages) <= policy.max_pages:
        raise PdfTextError("Cantidad de páginas PDFium inválida")
    pages: list[str] = []
    layout: list[tuple[PdfTextSpan, ...]] = []
    for expected, raw_page in enumerate(raw_pages, 1):
        if (not isinstance(raw_page, dict) or
                set(raw_page) != {"page_number", "width_points", "height_points", "rotation", "bbox", "text", "lines"} or
                raw_page.get("page_number") != expected):
            raise PdfTextError("Numeración PDFium inválida")
        text = raw_page.get("text")
        lines_raw = raw_page.get("lines")
        width = raw_page.get("width_points")
        height = raw_page.get("height_points")
        page_bbox = _finite_bbox(raw_page.get("bbox"))
        if (not isinstance(width, (int, float)) or not isinstance(height, (int, float)) or
                not math.isfinite(float(width)) or not math.isfinite(float(height)) or
                float(width) <= 0 or float(height) <= 0 or not isinstance(text, str) or
                len(text) > 2_000_000 or not isinstance(lines_raw, list) or len(lines_raw) > 100_000):
            raise PdfTextError("Texto PDFium fuera de límites")
        rotation = raw_page.get("rotation", 0)
        if rotation not in (0, 90, 180, 270):
            raise PdfTextError("Rotación PDFium inválida")
        page_lines: list[PdfTextSpan] = []
        for line in lines_raw:
            if (not isinstance(line, dict) or set(line) != {"text", "bbox", "cells"} or
                    not isinstance(line.get("text"), str) or len(line["text"]) > 100_000):
                raise PdfTextError("Línea PDFium inválida")
            bbox = _finite_bbox(line.get("bbox"))
            if not _bbox_within(bbox, page_bbox):
                raise PdfTextError("Línea PDFium fuera de la página")
            cells_raw = line.get("cells", [])
            if not isinstance(cells_raw, list) or len(cells_raw) > 1000:
                raise PdfTextError("Celdas PDFium inválidas")
            cells_list = []
            for cell in cells_raw:
                if (not isinstance(cell, dict) or set(cell) != {"text", "bbox"} or
                        not isinstance(cell.get("text"), str) or len(cell["text"]) > 10_000):
                    raise PdfTextError("Celdas PDFium inválidas")
                cell_bbox = _finite_bbox(cell["bbox"])
                if not _bbox_within(cell_bbox, bbox):
                    raise PdfTextError("Celda PDFium fuera de su línea")
                cells_list.append(PdfTextCell(cell["text"], cell_bbox))
            cells = tuple(cells_list)
            page_lines.append(PdfTextSpan(line["text"], bbox, cells))
        page_layout = tuple(_reconstruct_table_lines(page_lines))
        # The geometric stream is the source of truth for the worker route;
        # rebuilding page text from it keeps the text and evidence line
        # numbers synchronized after column reconstruction.
        text = "\n".join(span.text for span in page_layout)
        pages.append(text)
        page_layout = tuple(page_layout)
        layout.append(page_layout)
    if not any(page.strip() for page in pages):
        raise NoTextPdfError("El PDF no contiene texto legible")
    # Worker v1 exposes layout spans; page metadata is deliberately not copied
    # into the public PdfText model until a versioned geometry model is needed.
    return PdfText(pages=tuple(pages), raw="\f".join(pages), layout=tuple(layout))


def _finite_bbox(value: object) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise PdfTextError("BBox PDFium inválido")
    values = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in values) or values[2] <= values[0] or values[3] <= values[1]:
        raise PdfTextError("BBox PDFium inválido")
    return values


def _bbox_within(inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]) -> bool:
    return (outer[0] <= inner[0] <= inner[2] <= outer[2] and
            outer[1] <= inner[1] <= inner[3] <= outer[3])


def _reconstruct_table_lines(lines: list[object]) -> list[object]:
    """Merge PDFium word tokens into logical table columns.

    CAD/PDF text often has a larger gap between every word than between
    letters.  Splitting only by that gap incorrectly turns ``Emplantillado
    G10`` into two cells.  Once a header is present, rows are reconstructed
    by semantic order (item, description, unit, quantity) while retaining the
    original cell boxes for evidence.
    """
    from .pdf_backend import PdfTextSpan
    header = next((index for index, line in enumerate(lines)
                   if "item" in line.text.casefold() and "desc" in line.text.casefold()
                   and "cant" in line.text.casefold()), None)
    if header is None:
        return lines
    output = list(lines)
    unit_words = {"m3", "m²", "m2", "cm2", "cm²", "mm", "kg", "ton", "t", "un", "unidad", "ml", "l"}
    number = re.compile(r"^[-+]?\d[\d.,]*$")
    for index in range(header + 1, len(output)):
        line = output[index]
        cells = list(line.cells)
        if len(cells) < 4 or not number.fullmatch(cells[0].text.strip()) or not number.fullmatch(cells[-1].text.strip()):
            continue
        unit_index = next((pos for pos in range(1, len(cells) - 1)
                           if cells[pos].text.strip().casefold() in unit_words), None)
        if unit_index is None or unit_index == 1:
            continue
        item = cells[0].text.strip()
        description = " ".join(cell.text.strip() for cell in cells[1:unit_index])
        unit = cells[unit_index].text.strip()
        quantities = "  ".join(cell.text.strip() for cell in cells[unit_index + 1:])
        reconstructed = f"{item}  {description}  {unit}  {quantities}"
        output[index] = PdfTextSpan(reconstructed, line.bbox, line.cells)
    return output
