from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class PdfTextError(RuntimeError):
    pass


@dataclass(frozen=True)
class PdfText:
    pages: tuple[str, ...]
    raw: str = ""

    @property
    def full_text(self) -> str:
        return self.raw or "\f".join(self.pages)


def extract_layout_text(pdf_path: str | Path) -> PdfText:
    path = Path(pdf_path)
    if not path.is_file() or path.suffix.lower() != ".pdf":
        raise PdfTextError(f"PDF no válido: {path}")
    executable = shutil.which("pdftotext")
    if not executable:
        raise PdfTextError("No se encontró pdftotext (Poppler) en PATH")
    completed = subprocess.run(
        [executable, "-layout", "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise PdfTextError(completed.stderr.strip() or "pdftotext falló")
    # Solo se quita el separador final agregado por Poppler; las páginas vacías
    # intermedias se conservan para no desplazar la numeración del plano.
    raw_pages = completed.stdout.split("\f")
    if raw_pages and raw_pages[-1] == "":
        raw_pages.pop()
    pages = tuple(raw_pages)
    if not any(page.strip() for page in pages):
        raise PdfTextError("El PDF no contiene texto legible; OCR no está soportado")
    return PdfText(pages=pages, raw=completed.stdout)
