from pathlib import Path
import hashlib

from .adapters import HeuristicInterpreter, TableInterpreter
from .excel import export_excel
from .locator import locate_summary_tables
from .models import ExtractionResult
from .pdf_text import extract_layout_text


def process_pdf(pdf_path: str | Path, excel_path: str | Path | None = None, interpreter: TableInterpreter | None = None, text_path: str | Path | None = None) -> ExtractionResult:
    path = Path(pdf_path)
    document = extract_layout_text(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if text_path is not None:
        target = Path(text_path); target.write_bytes(document.full_text.encode("utf-8"))
        target.with_suffix(target.suffix + ".pages").mkdir(parents=True, exist_ok=True)
        for number, page in enumerate(document.pages, 1):
            (target.with_suffix(target.suffix + ".pages") / f"page-{number:04d}.txt").write_bytes(page.encode("utf-8"))
    candidates = locate_summary_tables(document)
    if not candidates:
        extracted_characters = sum(len("".join(page.split())) for page in document.pages)
        warnings = [
            "No se encontró una 'Tabla de cubicación' o 'Cuadro de cubicación' en el texto extraíble"
        ]
        if extracted_characters < 500:
            warnings.append(
                "El PDF contiene muy poco texto seleccionable. La tabla puede verse en pantalla, "
                "pero estar dibujada como geometría CAD; este alcance no utiliza OCR"
            )
        result = ExtractionResult(archivo=path.name, tabla_encontrada=False, titulo_tabla=None, filas=[], requiere_revision=extracted_characters < 500, advertencias=warnings, sha256=digest, page_count=len(document.pages), extractor_version="pdftotext-layout")
    else:
        close_candidates = [value for value in candidates[1:] if value.score >= candidates[0].score - 5]
        if close_candidates:
            result = ExtractionResult(archivo=path.name, tabla_encontrada=True, titulo_tabla=None, filas=[], requiere_revision=True, candidatos=[f"p.{v.page}: {v.title}" for v in candidates], advertencias=["Varias tablas equivalentes; seleccione una"], sha256=digest, page_count=len(document.pages), extractor_version="pdftotext-layout")
        else:
            result = (interpreter or HeuristicInterpreter()).interpret(candidates[0], path.name)
            result.sha256, result.page_count, result.extractor_version = digest, len(document.pages), "pdftotext-layout"
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
        export_excel(result, excel_path)
    return result
