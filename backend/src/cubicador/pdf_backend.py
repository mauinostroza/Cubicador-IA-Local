"""Backends de PDF con un contrato común y sin procesos externos.

El backend PDFium es una ruta de migración para sustituir Poppler en equipos
Windows.  ``pypdfium2`` se importa de forma diferida y no forma parte de las
dependencias base: si no está instalado, la operación falla cerrada.  El
backend no descarga DLL, modelos ni archivos y no busca ejecutables en PATH.

Las coordenadas del contrato están en puntos PDF y usan el sistema PDF
(origen abajo-izquierda): ``(left, bottom, right, top)``.  Las coordenadas de
imagen usan origen arriba-izquierda y se entregan únicamente en el resultado
del render.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from statistics import median
import threading

from .security import SecurityPolicy, SecurityViolation, ensure_within


class PdfBackendError(RuntimeError):
    """Error controlado de un backend PDF."""


class PdfiumUnavailable(PdfBackendError):
    """PDFium no está instalado o no puede cargarse."""


@dataclass(frozen=True, slots=True)
class PdfTextSpan:
    """Fragmento de texto con evidencia geométrica en puntos PDF."""

    text: str
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class PdfPageText:
    page_number: int
    width_points: float
    height_points: float
    text: str
    lines: tuple[PdfTextSpan, ...]


@dataclass(frozen=True, slots=True)
class PdfRender:
    page_number: int
    output: Path
    width: int
    height: int
    dpi: int
    crop_points: tuple[float, float, float, float] | None = None


class PdfBackend(Protocol):
    """Contrato mínimo requerido por el pipeline y por pruebas fake."""

    def page_count(self, pdf_path: Path, *, policy: SecurityPolicy) -> int: ...

    def extract_page(self, pdf_path: Path, page_number: int, *, policy: SecurityPolicy) -> PdfPageText: ...

    def render_page(self, pdf_path: Path, page_number: int, *, dpi: int, output: Path,
                    policy: SecurityPolicy, workspace: Path,
                    cancel: threading.Event | None = None) -> PdfRender: ...

    def render_crop(self, pdf_path: Path, page_number: int,
                    crop_points: tuple[float, float, float, float], *, dpi: int,
                    output: Path, policy: SecurityPolicy, workspace: Path,
                    cancel: threading.Event | None = None) -> PdfRender: ...


def _check_page(page_number: int, count: int) -> None:
    if not isinstance(page_number, int) or not 1 <= page_number <= count:
        raise PdfBackendError("Número de página fuera de rango")


def _check_bbox(box: tuple[float, float, float, float], page_bbox: tuple[float, float, float, float]) -> None:
    if len(box) != 4 or any(not isinstance(value, (int, float)) for value in box):
        raise PdfBackendError("Recorte PDF inválido")
    left, bottom, right, top = box
    page_left, page_bottom, page_right, page_top = page_bbox
    if not (page_left <= left < right <= page_right and page_bottom <= bottom < top <= page_top):
        raise PdfBackendError("Recorte PDF fuera de la página")


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel and cancel.is_set():
        raise PdfBackendError("Trabajo cancelado")


class PdfiumBackend:
    """Implementación PDFium/pypdfium2 con activación explícita.

    ``SecurityPolicy.pdfium_enabled`` queda desactivado por defecto.  Esta
    clase se puede probar con un PDF local cuando se habilita expresamente,
    pero el release actual no incluye el wheel ni su DLL PDFium.
    """

    engine = "PDFium"

    def __init__(self, *, policy: SecurityPolicy):
        if not policy.pdfium_enabled or not policy.pdfium_worker_enabled:
            raise PdfiumUnavailable("PDFium está desactivado o no está dentro de un worker autorizado")
        try:
            import pypdfium2 as pdfium
        except (ImportError, OSError) as exc:
            raise PdfiumUnavailable("pypdfium2/PDFium no está instalado") from exc
        self._pdfium = pdfium

    @staticmethod
    def from_policy(policy: SecurityPolicy) -> "PdfiumBackend":
        return PdfiumBackend(policy=policy)

    def _open(self, pdf_path: Path, policy: SecurityPolicy):
        path = policy.validate_pdf(pdf_path)
        try:
            return self._pdfium.PdfDocument(str(path))
        except Exception as exc:
            raise PdfBackendError("PDFium no pudo abrir el PDF") from exc

    def page_count(self, pdf_path: Path, *, policy: SecurityPolicy) -> int:
        document = self._open(pdf_path, policy)
        try:
            count = len(document)
        finally:
            close = getattr(document, "close", None)
            if close:
                close()
        if not 1 <= count <= policy.max_pages:
            raise PdfBackendError(f"Cantidad de páginas fuera del límite ({policy.max_pages})")
        return count

    def extract_page(self, pdf_path: Path, page_number: int, *, policy: SecurityPolicy) -> PdfPageText:
        document = self._open(pdf_path, policy)
        try:
            _check_page(page_number, len(document))
            page = document[page_number - 1]
            width, height = page.get_size()
            textpage = page.get_textpage()
            try:
                lines = _text_lines(textpage)
                text = "\n".join(line.text for line in lines)
            finally:
                close_text = getattr(textpage, "close", None)
                if close_text:
                    close_text()
            return PdfPageText(page_number, float(width), float(height), text, tuple(lines))
        except PdfBackendError:
            raise
        except Exception as exc:
            raise PdfBackendError("PDFium no pudo extraer texto") from exc
        finally:
            close_page = locals().get("page")
            if close_page is not None:
                close = getattr(close_page, "close", None)
                if close:
                    close()
            close = getattr(document, "close", None)
            if close:
                close()

    def render_page(self, pdf_path: Path, page_number: int, *, dpi: int, output: Path,
                    policy: SecurityPolicy, workspace: Path,
                    cancel: threading.Event | None = None) -> PdfRender:
        return self._render(pdf_path, page_number, dpi=dpi, output=output, policy=policy,
                            workspace=workspace, cancel=cancel, crop_points=None)

    def render_crop(self, pdf_path: Path, page_number: int,
                    crop_points: tuple[float, float, float, float], *, dpi: int,
                    output: Path, policy: SecurityPolicy, workspace: Path,
                    cancel: threading.Event | None = None) -> PdfRender:
        document = self._open(pdf_path, policy)
        try:
            _check_page(page_number, len(document))
            page = document[page_number - 1]
            left, bottom, right, top = page.get_bbox()
            _check_bbox(crop_points, (float(left), float(bottom), float(right), float(top)))
        finally:
            close_page = locals().get("page")
            if close_page is not None:
                close = getattr(close_page, "close", None)
                if close:
                    close()
            close = getattr(document, "close", None)
            if close:
                close()
        return self._render(pdf_path, page_number, dpi=dpi, output=output, policy=policy,
                            workspace=workspace, cancel=cancel, crop_points=crop_points)

    def _render(self, pdf_path: Path, page_number: int, *, dpi: int, output: Path,
                policy: SecurityPolicy, workspace: Path,
                cancel: threading.Event | None,
                crop_points: tuple[float, float, float, float] | None) -> PdfRender:
        if not isinstance(dpi, int) or not 30 <= dpi <= 600:
            raise PdfBackendError("DPI fuera de límites")
        _check_cancel(cancel)
        document = self._open(pdf_path, policy)
        try:
            _check_page(page_number, len(document))
            page = document[page_number - 1]
            page_left, page_bottom, page_right, page_top = page.get_bbox()
            width, height = page_right - page_left, page_top - page_bottom
            crop = None
            if crop_points is not None:
                _check_bbox(crop_points, (float(page_left), float(page_bottom), float(page_right), float(page_top)))
                left, bottom, right, top = crop_points
                # PDFium recibe márgenes desde el CropBox, no un bbox.
                crop = (left - page_left, bottom - page_bottom, page_right - right, page_top - top)
            render_width = max(1, round((right - left) * dpi / 72)) if crop_points else max(1, round(width * dpi / 72))
            render_height = max(1, round((top - bottom) * dpi / 72)) if crop_points else max(1, round(height * dpi / 72))
            _check_render_budget(render_width, render_height, policy)
            bitmap = page.render(scale=dpi / 72, **({"crop": crop} if crop is not None else {}))
            try:
                image = bitmap.to_pil()
                try:
                    target = ensure_within(output, workspace)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    image.save(target, format="PNG")
                    image_width, image_height = image.size
                finally:
                    close_image = getattr(image, "close", None)
                    if close_image:
                        close_image()
            finally:
                close_bitmap = getattr(bitmap, "close", None)
                if close_bitmap:
                    close_bitmap()
            if image_width * image_height > policy.max_ocr_detail_pixels:
                raise SecurityViolation("Render PDFium excede el límite de píxeles")
            return PdfRender(page_number, target, image_width, image_height, dpi, crop_points)
        except PdfBackendError:
            raise
        except (OSError, ValueError) as exc:
            raise PdfBackendError("PDFium no pudo renderizar el PDF") from exc
        finally:
            close_page = locals().get("page")
            if close_page is not None:
                close = getattr(close_page, "close", None)
                if close:
                    close()
            close = getattr(document, "close", None)
            if close:
                close()


def _text_lines(textpage: object) -> list[PdfTextSpan]:
    """Agrupa glifos por saltos de línea conservando texto y bbox.

    PDFium entrega cajas de glifos en puntos. Los espacios/saltos suelen tener
    caja nula; se conservan en el texto, pero no se usan para calcular la
    geometría. Si no hay cajas, la línea se conserva con bbox nulo implícito
    mediante una caja cero, permitiendo que el consumidor la marque para
    revisión.
    """
    # Se itera sobre índices internos de PDFium y se consulta cada carácter
    # individualmente. Esto evita confundir índices UCS-2/texto con índices de
    # caracteres PDFium cuando el PDF contiene glifos excluidos o insertados.
    records: list[tuple[str, tuple[float, float, float, float]]] = []
    count = int(textpage.count_chars())
    if count > 2_000_000:
        raise PdfBackendError("Texto PDFium excede el límite de caracteres")
    for index in range(count):
        try:
            value = textpage.get_text_range(index, 1)
            box = tuple(float(v) for v in textpage.get_charbox(index))
        except Exception:
            continue
        # PDFium representa espacios con una caja de altura casi nula. Se
        # omiten como glifos y se reconstruyen por la separación horizontal;
        # de ese modo no crean una fila propia.
        if (not value or value.isspace() or len(box) != 4 or box[2] <= box[0]
                or box[3] - box[1] < 0.25):
            continue
        records.append((value, box))  # type: ignore[arg-type]
    if not records:
        return []
    # PDFium entrega cajas en coordenadas PDF. Agrupar por cercanía vertical y
    # ordenar por x reconstruye una disposición de columnas verificable; no se
    # afirma que esto sea una tabla semántica.
    grouped: list[list[tuple[str, tuple[float, float, float, float]]]] = []
    for record in sorted(records, key=lambda value: (-((value[1][1] + value[1][3]) / 2), value[1][0])):
        center = (record[1][1] + record[1][3]) / 2
        height = record[1][3] - record[1][1]
        target = next((row for row in grouped
                       if abs(((row[0][1][1] + row[0][1][3]) / 2) - center)
                       <= max(2.0, height * .55, _row_height(row) * .55)), None)
        if target is None:
            grouped.append([record])
        else:
            target.append(record)
    result = []
    for row in grouped:
        row.sort(key=lambda value: value[1][0])
        chars: list[str] = []
        previous_right = None
        widths = [box[2] - box[0] for _, box in row]
        nominal = max(1.0, float(median(widths)))
        for char, box in row:
            gap = 0 if previous_right is None else box[0] - previous_right
            if gap > nominal * .60:
                chars.append(" " * max(1, round(gap / nominal)))
            chars.append(char.replace("\r", "").replace("\n", ""))
            previous_right = box[2]
        result.append(_make_span("".join(chars), [box for _, box in row]))
    return result


def _row_height(row: list[tuple[str, tuple[float, float, float, float]]]) -> float:
    """Altura robusta de una fila, ignorando glifos de puntuación pequeños."""
    heights = [box[3] - box[1] for _, box in row if box[3] - box[1] >= 1.0]
    return float(median(heights)) if heights else 1.0


def _check_render_budget(width: int, height: int, policy: SecurityPolicy) -> None:
    pixels = width * height
    if pixels <= 0 or pixels > policy.max_ocr_detail_pixels:
        raise SecurityViolation("Render PDFium excede el límite de píxeles antes de asignar memoria")
    # Un bitmap RGBA sin comprimir puede cuadruplicar esta cifra; bloquear antes
    # de renderizar evita que PNG compression oculte el consumo real.
    if pixels * 4 > policy.max_ocr_image_bytes:
        raise SecurityViolation("Render PDFium excede el límite de memoria de imagen")


def _make_span(text: str, boxes: list[tuple[float, float, float, float]]) -> PdfTextSpan:
    if not boxes:
        return PdfTextSpan(text, (0.0, 0.0, 0.0, 0.0))
    return PdfTextSpan(text, (min(v[0] for v in boxes), min(v[1] for v in boxes),
                              max(v[2] for v in boxes), max(v[3] for v in boxes)))


def feature_enabled(policy: SecurityPolicy) -> bool:
    """Consulta explícita del gate; no se activa por variables del entorno."""
    return bool(policy.pdfium_enabled and policy.pdfium_worker_enabled)
