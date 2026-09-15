from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Evidence(BaseModel):
    page: int = Field(ge=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    text: str = Field(min_length=1)


class QuantityValue(BaseModel):
    column: str
    original: str
    numeric_value: float | None
    parse_status: str = Field(pattern="^(parsed|ambiguous|invalid)$")
    warning: str | None = None


class QuantityRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1)
    descripcion: str = Field(min_length=1)
    unidad: str = Field(min_length=1)
    cells_original: list[str]
    quantities: list[QuantityValue]
    tipo_fila: str = Field(default="detalle", pattern="^(detalle|subtotal|total|nota)$")
    pagina: int = Field(ge=1)
    evidencia: Evidence

    @field_validator("item", "descripcion", "unidad")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return " ".join(value.split())


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archivo: str
    tabla_encontrada: bool
    titulo_tabla: str | None
    columns: list[str] = Field(default_factory=list)
    filas: list[QuantityRow]
    requiere_revision: bool = False
    candidatos: list[str] = Field(default_factory=list)
    advertencias: list[str] = Field(default_factory=list)
    sha256: str
    page_count: int = Field(ge=1)
    extractor_version: str

    @field_validator("archivo")
    @classmethod
    def basename_only(cls, value: str) -> str:
        return Path(value).name
