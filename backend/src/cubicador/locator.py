from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .pdf_text import PdfText


TITLE_WORDS = (
    "tabla de cubicacion",
    "cuadro de cubicacion",
    "tabla resumen de cantidades",
    "resumen de cantidades",
    "cuadro de cantidades",
    "resumen cantidades",
    "cantidades de obra",
)
HEADER_WORDS = ("item", "ítem", "descripcion", "descripción", "unidad", "cantidad")


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


@dataclass(frozen=True)
class TableCandidate:
    title: str
    page: int
    text: str
    score: int


def locate_summary_tables(document: PdfText) -> list[TableCandidate]:
    candidates: list[TableCandidate] = []
    for page_number, page in enumerate(document.pages, 1):
        lines = page.splitlines()
        for index, line in enumerate(lines):
            folded = _fold(line)
            title_score = max((len(words) for words in TITLE_WORDS if words in folded), default=0)
            if not title_score:
                continue
            end = len(lines)
            for cursor in range(index + 2, len(lines)):
                candidate = _fold(lines[cursor])
                if re.match(r"^\s*(notas?|observaciones?|[0-9]+\.\s+[a-z])", candidate):
                    end = cursor
                    break
            block = "\n".join(lines[index:end]).rstrip()
            header_hits = sum(word in _fold(block[:1000]) for word in HEADER_WORDS)
            numeric_lines = sum(bool(re.search(r"\d", value)) for value in block.splitlines()[1:])
            candidates.append(TableCandidate(line.strip(), page_number, block, title_score + 10 * header_hits + numeric_lines))
    return sorted(candidates, key=lambda value: value.score, reverse=True)


def locate_summary_table(document: PdfText) -> TableCandidate | None:
    candidates = locate_summary_tables(document)
    return candidates[0] if candidates else None
