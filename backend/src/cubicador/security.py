from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
import re


class SecurityViolation(ValueError):
    """Entrada rechazada por la política local de ejecución."""


@dataclass(frozen=True, slots=True)
class SecurityPolicy:
    max_pdf_bytes: int = 50 * 1024 * 1024
    max_output_bytes: int = 25 * 1024 * 1024
    max_temp_bytes: int = 250 * 1024 * 1024
    max_response_bytes: int = 2 * 1024 * 1024
    max_process_output_bytes: int = 2 * 1024 * 1024
    subprocess_timeout_seconds: int = 120
    model_timeout_seconds: int = 120
    max_concurrent_jobs: int = 1
    max_queued_jobs: int = 2
    max_pages: int = 200
    allowed_model_ports: tuple[int, ...] = (8080,)

    def __post_init__(self) -> None:
        positive = (
            self.max_pdf_bytes, self.max_output_bytes, self.max_temp_bytes,
            self.max_response_bytes, self.max_process_output_bytes,
            self.subprocess_timeout_seconds, self.model_timeout_seconds,
            self.max_concurrent_jobs, self.max_pages,
        )
        if any(value <= 0 for value in positive) or self.max_queued_jobs < 0:
            raise ValueError("Todos los límites deben ser positivos y la cola no negativa")
        if self.max_output_bytes > self.max_temp_bytes or self.max_response_bytes > self.max_temp_bytes:
            raise ValueError("Las salidas y respuestas no pueden superar la cuota temporal")
        if not self.allowed_model_ports or any(not 1 <= port <= 65535 for port in self.allowed_model_ports):
            raise ValueError("Los puertos permitidos deben estar entre 1 y 65535")

    def validate_pdf(self, value: str | Path) -> Path:
        _reject_unsafe_path_text(value)
        supplied = Path(value).expanduser()
        if supplied.is_symlink():
            raise SecurityViolation("No se admiten enlaces simbólicos")
        path = supplied.resolve(strict=True)
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise SecurityViolation("La entrada debe ser un archivo PDF existente")
        size = path.stat().st_size
        if size <= 0 or size > self.max_pdf_bytes:
            raise SecurityViolation(f"PDF fuera del límite permitido ({self.max_pdf_bytes} bytes)")
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise SecurityViolation("La firma del archivo no corresponde a un PDF")
        return path

    def validate_local_endpoint(self, endpoint: str) -> str:
        parsed = urlsplit(endpoint)
        if parsed.scheme != "http" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise SecurityViolation("El modelo solo admite HTTP local sin credenciales, consulta ni fragmento")
        if parsed.hostname != "127.0.0.1":
            raise SecurityViolation("El endpoint del modelo debe usar 127.0.0.1 literalmente")
        if parsed.port not in self.allowed_model_ports:
            raise SecurityViolation("Puerto del modelo no autorizado")
        if parsed.path != "/v1/chat/completions":
            raise SecurityViolation("Ruta del endpoint no autorizada")
        return endpoint


DEFAULT_SECURITY_POLICY = SecurityPolicy()


def ensure_within(path: str | Path, root: str | Path) -> Path:
    _reject_unsafe_path_text(path)
    resolved_root = Path(root).resolve(strict=True)
    candidate = Path(path).resolve(strict=False)
    if not candidate.is_relative_to(resolved_root):
        raise SecurityViolation("Ruta fuera del espacio autorizado")
    return candidate


def _reject_unsafe_path_text(value: str | Path) -> None:
    raw = str(value)
    if raw.startswith(("\\\\", "//")):
        raise SecurityViolation("No se admiten rutas UNC o de red")
    # Rechaza Alternate Data Streams de Windows, conservando C:\ como prefijo válido.
    tail = raw[2:] if re.match(r"^[A-Za-z]:[\\/]", raw) else raw
    if ":" in tail:
        raise SecurityViolation("No se admiten Alternate Data Streams")
