from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from .security import DEFAULT_SECURITY_POLICY, SecurityPolicy
from .runtime import AuditLog, run_command


class PdfTextError(RuntimeError):
    pass


@dataclass(frozen=True)
class PdfText:
    pages: tuple[str, ...]
    raw: str = ""

    @property
    def full_text(self) -> str:
        return self.raw or "\f".join(self.pages)


def extract_layout_text(pdf_path: str | Path, policy: SecurityPolicy, workspace: Path, audit: AuditLog | None = None) -> PdfText:
    try:
        path = policy.validate_pdf(pdf_path)
    except (ValueError, OSError) as exc:
        raise PdfTextError(str(exc)) from exc
    candidates = (
        Path(__file__).resolve().parents[3] / "vendor" / "poppler" / "bin" / ("pdftotext.exe" if __import__("os").name == "nt" else "pdftotext"),
        Path("/usr/bin/pdftotext"),
        Path("/usr/local/bin/pdftotext"),
    )
    executable_path = next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)
    if executable_path is None:
        raise PdfTextError("No se encontró pdftotext en la ubicación controlada de Poppler")
    completed = run_command(
        [str(executable_path), "-layout", "-enc", "UTF-8", str(path), "-"],
        policy=policy, workspace=workspace, audit=audit,
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
        raise PdfTextError("El PDF no contiene texto legible; OCR no está soportado")
    return PdfText(pages=pages, raw=stdout)
