from pathlib import Path
from typing import Annotated
import re

from pydantic import BaseModel, ConfigDict, Field, field_validator


# C0 (salvo \t y \n) y C1: el CLI imprime este JSON a stdout, donde un ESC inyectado sería interpretado.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]")


def _strip_control_chars(value: str) -> str:
    return _CONTROL_CHARS_RE.sub("", value)


def _clean_single_line(value: str) -> str:
    """Sanea y colapsa espacios para campos de una sola línea."""
    return " ".join(_strip_control_chars(value).split())


def _clean_line_list(values: list[str]) -> list[str]:
    return [_clean_single_line(value) for value in values]


def _clean_evidence_text(value: str) -> str:
    """Sanea sin colapsar espacios: el parser heurístico usa los dobles espacios como separador de columnas."""
    return _strip_control_chars(value)


class Evidence(BaseModel):
    page: int = Field(ge=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=4000)
    bbox: tuple[int, int, int, int] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    dpi: int | None = Field(default=None, ge=1)
    crop_sha256: str | None = Field(default=None, pattern="^[0-9a-f]{64}$")
    model_hashes: dict[str, str] = Field(default_factory=dict)
    coordinate_frame: str | None = None
    pdf_bbox_points: tuple[float, float, float, float] | None = None
    engine: str | None = None
    engine_version: str | None = None
    model_version: str | None = None

    @field_validator("text")
    @classmethod
    def clean_text_field(cls, value: str) -> str:
        return _clean_evidence_text(value)


class QuantityValue(BaseModel):
    column: str = Field(max_length=120)
    original: str = Field(max_length=120)
    numeric_value: float | None
    parse_status: str = Field(pattern="^(parsed|ambiguous|invalid)$")
    warning: str | None = Field(default=None, max_length=300)
    bbox: tuple[int, int, int, int] | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("column", "original")
    @classmethod
    def clean_required_text(cls, value: str) -> str:
        return _clean_single_line(value)

    @field_validator("warning")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return _clean_single_line(value) if value is not None else value


class QuantityRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1, max_length=200)
    descripcion: str = Field(min_length=1, max_length=500)
    unidad: str = Field(min_length=1, max_length=32)
    cells_original: list[Annotated[str, Field(max_length=500)]]
    quantities: list[QuantityValue]
    tipo_fila: str = Field(default="detalle", pattern="^(detalle|subtotal|total|nota)$")
    pagina: int = Field(ge=1)
    evidencia: Evidence

    @field_validator("item", "descripcion", "unidad")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return _clean_single_line(value)

    @field_validator("cells_original")
    @classmethod
    def clean_cells_original(cls, value: list[str]) -> list[str]:
        return _clean_line_list(value)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archivo: str = Field(max_length=255)
    tabla_encontrada: bool
    titulo_tabla: str | None = Field(max_length=300)
    columns: list[Annotated[str, Field(max_length=120)]] = Field(default_factory=list)
    filas: list[QuantityRow]
    requiere_revision: bool = False
    candidatos: list[Annotated[str, Field(max_length=300)]] = Field(default_factory=list)
    advertencias: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list)
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    extractor_version: str = Field(max_length=64)

    @field_validator("archivo")
    @classmethod
    def basename_only(cls, value: str) -> str:
        return Path(value).name

    @field_validator("titulo_tabla", "extractor_version")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return _clean_single_line(value) if value is not None else value

    @field_validator("columns", "candidatos", "advertencias")
    @classmethod
    def clean_text_lists(cls, value: list[str]) -> list[str]:
        return _clean_line_list(value)
