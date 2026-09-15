from pathlib import Path
import hashlib

from .adapters import HeuristicInterpreter, TableInterpreter
from .excel import export_excel
from .locator import locate_summary_tables
from .models import ExtractionResult
from .pdf_text import NoTextPdfError, PdfText, extract_layout_text, get_page_count
from .ocr import OcrError, OcrProvider, ocr_document
from .runtime import AuditStore, JobGate, JobWorkspace, atomic_write_bytes, safe_output_path
from .security import DEFAULT_SECURITY_POLICY, SecurityPolicy, SecurityViolation


_JOB_GATE = JobGate(DEFAULT_SECURITY_POLICY)


def _match_quantity_cell(quantity_text: str, cells: list[dict]) -> dict | None:
    """Return a cell only when its normalized text identifies it uniquely."""
    needle = "".join(quantity_text.split()).casefold()
    if not needle:
        return None
    exact = [cell for cell in cells if "".join(str(cell["text"]).split()).casefold() == needle]
    return exact[0] if len(exact) == 1 else None


def process_pdf(pdf_path: str | Path, excel_path: str | Path | None = None, interpreter: TableInterpreter | None = None, text_path: str | Path | None = None, *, output_root: str | Path | None = None, policy: SecurityPolicy = DEFAULT_SECURITY_POLICY, ocr_provider: OcrProvider | None = None) -> ExtractionResult:
    path = policy.validate_pdf(pdf_path)
    if (excel_path is not None or text_path is not None) and output_root is None:
        raise SecurityViolation("output_root es obligatorio para cualquier escritura")
    excel_target = safe_output_path(excel_path, output_root, ".xlsx") if excel_path is not None else None
    text_target = safe_output_path(text_path, output_root, ".txt") if text_path is not None else None
    gate = _JOB_GATE if policy is DEFAULT_SECURITY_POLICY else JobGate(policy)
    audit = AuditStore(output_root, policy).start_job() if output_root is not None else None
    if audit:
        audit.append("job_started", "ok", bytes=path.stat().st_size)
    job_workspace = JobWorkspace(policy)
    try:
        with gate, job_workspace as workspace:
            result = _process_pdf(path, excel_target, interpreter, text_target, policy, workspace, audit, ocr_provider)
            job_workspace.check_quota()
        if audit:
            audit.append("job_completed", "ok")
        return result
    except Exception as exc:
        if audit:
            audit.append("job_failed", "error", detail_code=type(exc).__name__)
        raise


def _process_pdf(path: Path, excel_path: Path | None, interpreter: TableInterpreter | None, text_path: Path | None, policy: SecurityPolicy, workspace: Path, audit=None, ocr_provider: OcrProvider | None = None) -> ExtractionResult:
    try:
        document = extract_layout_text(path, policy, workspace, audit)
        page_count = len(document.pages)
    except NoTextPdfError:
        if ocr_provider is None:
            raise
        page_count = get_page_count(path, policy, workspace, audit)
        document = PdfText(tuple("" for _ in range(page_count)))
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    candidates = locate_summary_tables(document)
    extraction_method = "pdftotext-layout"
    extracted_characters = sum(len("".join(page.split())) for page in document.pages)
    if not candidates and extracted_characters < 500 and ocr_provider is not None:
        try:
            if audit:
                audit.append("ocr_started", "ok", bytes=path.stat().st_size)
            document = ocr_document(path, page_count, ocr_provider, policy, workspace, audit)
            candidates = locate_summary_tables(document)
            extraction_method = "ocr-local"
            extracted_characters = sum(len("".join(page.split())) for page in document.pages)
            if audit:
                audit.append("ocr_completed", "ok")
        except OcrError:
            # No se acepta texto parcial ni se inventan filas: el resultado queda pendiente.
            candidates = []
            if audit:
                audit.append("ocr_failed", "error", detail_code="OcrError")
    if text_path is not None:
        atomic_write_bytes(text_path, document.full_text.encode("utf-8"), policy)
    if not candidates:
        warnings = [
            "No se encontró una 'Tabla de cubicación' o 'Cuadro de cubicación' en el texto extraíble"
        ]
        if extracted_characters < 500:
            warnings.append(
                "El PDF contiene muy poco texto seleccionable. La tabla puede verse en pantalla, "
                "pero estar dibujada como geometría CAD; OCR local no estuvo disponible o no encontró el título"
            )
        result = ExtractionResult(archivo=path.name, tabla_encontrada=False, titulo_tabla=None, filas=[], requiere_revision=extracted_characters < 500, advertencias=warnings, sha256=digest, page_count=len(document.pages), extractor_version=extraction_method)
    else:
        close_candidates = [value for value in candidates[1:] if value.score >= candidates[0].score - 5]
        if close_candidates:
            result = ExtractionResult(archivo=path.name, tabla_encontrada=True, titulo_tabla=None, filas=[], requiere_revision=True, candidatos=[f"p.{v.page}: {v.title}" for v in candidates], advertencias=["Varias tablas equivalentes; seleccione una"], sha256=digest, page_count=len(document.pages), extractor_version=extraction_method)
        else:
            result = (interpreter or HeuristicInterpreter()).interpret(candidates[0], path.name)
            result.sha256, result.page_count, result.extractor_version = digest, len(document.pages), extraction_method
            ambiguous_geometry = False
            if document.ocr_evidence and candidates[0].page in document.ocr_evidence:
                metadata = document.ocr_evidence[candidates[0].page]
                for row in result.filas:
                    source_rows = metadata["rows"][row.evidencia.line_start-1:row.evidencia.line_end]
                    if source_rows:
                        row.evidencia.bbox = (min(v["bbox"][0] for v in source_rows), min(v["bbox"][1] for v in source_rows),
                                              max(v["bbox"][2] for v in source_rows), max(v["bbox"][3] for v in source_rows))
                        row.evidencia.confidence = min(v["confidence"] for v in source_rows)
                    row.evidencia.dpi = metadata["dpi"]
                    row.evidencia.crop_sha256 = metadata["crop_sha256"]
                    row.evidencia.model_hashes = metadata["model_hashes"]
                    row.evidencia.coordinate_frame = "detail-crop-pixels"
                    row.evidencia.engine = metadata["engine"]
                    row.evidencia.engine_version = metadata["engine_version"]
                    row.evidencia.model_version = metadata["model_version"]
                    if row.evidencia.bbox:
                        tx0, ty0, tx1, ty1 = metadata["pdf_bbox_points"]
                        dw, dh = metadata["detail_size"]
                        rx0, ry0, rx1, ry1 = row.evidencia.bbox
                        row.evidencia.pdf_bbox_points = (tx0 + rx0*(tx1-tx0)/dw, ty0 + ry0*(ty1-ty0)/dh,
                                                        tx0 + rx1*(tx1-tx0)/dw, ty0 + ry1*(ty1-ty0)/dh)
                    cells = [cell for source_row in source_rows for cell in source_row["cells"]]
                    for quantity in row.quantities:
                        match = _match_quantity_cell(quantity.original, cells)
                        if match:
                            quantity.bbox = match["bbox"]
                            quantity.confidence = match["confidence"]
                        else:
                            ambiguous_geometry = True
            if ambiguous_geometry:
                result.requiere_revision = True
                result.advertencias.append("Evidencia geométrica de una o más cantidades es ambigua")
            result.candidatos = [f"p.{value.page}: {value.title}" for value in candidates]
            source_lines = candidates[0].text.splitlines()
            invalid_evidence = [row.item for row in result.filas if row.evidencia.page != candidates[0].page or row.evidencia.line_end < row.evidencia.line_start or row.evidencia.line_end > len(source_lines) or "\n".join(line.strip() for line in source_lines[row.evidencia.line_start - 1:row.evidencia.line_end]) != row.evidencia.text]
            ambiguous = [row.item for row in result.filas if any(q.parse_status != "parsed" for q in row.quantities)]
            if invalid_evidence:
                result.filas = []
                result.requiere_revision = True
                result.advertencias.append("Evidencia no verificable contra el bloque candidato")
            if ambiguous:
                result.requiere_revision = True
                result.advertencias.append("Valores numéricos ambiguos: " + ", ".join(ambiguous))
            if not result.filas:
                result.requiere_revision = True
    if excel_path is not None:
        export_excel(result, excel_path, policy.max_output_bytes)
    return result
