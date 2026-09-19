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
    max_ocr_pages: int = 5
    ocr_search_dpi: int = 150
    ocr_detail_dpi: int = 300
    max_ocr_search_pixels: int = 16_000_000
    max_ocr_detail_pixels: int = 12_000_000
    max_ocr_total_pixels: int = 92_000_000
    max_ocr_calls: int = 6
    max_ocr_image_bytes: int = 80 * 1024 * 1024
    allowed_model_ports: tuple[int, ...] = (8080,)
    windows_job_memory_bytes: int = 2 * 1024 * 1024 * 1024
    windows_process_memory_bytes: int = 1536 * 1024 * 1024
    windows_active_process_limit: int = 4
    windows_cpu_rate_percent: int = 80
    max_audit_bytes: int = 1024 * 1024
    max_audit_total_bytes: int = 10 * 1024 * 1024
    max_audit_files: int = 100
    windows_job_objects_enabled: bool = True
    termination_grace_seconds: int = 5
    developer_tools_enabled: bool = False
    # PDFium/pypdfium2 permanece en modo de evaluación: no se invoca desde el
    # pipeline/API hasta que exista un worker aislado y un release firmado.
    pdfium_enabled: bool = False
    # Solo un worker supervisado puede abrir PDFium. El valor se mantiene falso
    # en el runtime/API y se habilitará únicamente dentro del worker firmado.
    pdfium_worker_enabled: bool = False
    # Perfil de ejecución CPU para equipos Windows 10/11 con 8 GB de RAM.
    # El runner OCR recibe estos valores mediante un entorno mínimo y no puede
    # heredar la configuración del usuario ni crear trabajos concurrentes.
    cpu_profile: str = "intel-i3-8gb"
    model_threads: int = 2

    def __post_init__(self) -> None:
        positive = (
            self.max_pdf_bytes, self.max_output_bytes, self.max_temp_bytes,
            self.max_response_bytes, self.max_process_output_bytes,
            self.subprocess_timeout_seconds, self.model_timeout_seconds,
            self.max_concurrent_jobs, self.max_pages,
            self.max_ocr_pages, self.ocr_search_dpi, self.ocr_detail_dpi,
            self.max_ocr_search_pixels, self.max_ocr_detail_pixels,
            self.max_ocr_total_pixels, self.max_ocr_calls,
            self.max_ocr_image_bytes,
            self.windows_job_memory_bytes, self.windows_process_memory_bytes,
            self.windows_active_process_limit, self.windows_cpu_rate_percent,
            self.max_audit_bytes,
            self.max_audit_total_bytes, self.max_audit_files,
            self.termination_grace_seconds,
            self.model_threads,
        )
        if any(value <= 0 for value in positive) or self.max_queued_jobs < 0:
            raise ValueError("Todos los límites deben ser positivos y la cola no negativa")
        if self.max_output_bytes > self.max_temp_bytes or self.max_response_bytes > self.max_temp_bytes:
            raise ValueError("Las salidas y respuestas no pueden superar la cuota temporal")
        if not self.allowed_model_ports or any(not 1 <= port <= 65535 for port in self.allowed_model_ports):
            raise ValueError("Los puertos permitidos deben estar entre 1 y 65535")
        if self.windows_process_memory_bytes > self.windows_job_memory_bytes:
            raise ValueError("La memoria por proceso no puede superar la memoria del Job")
        if self.windows_cpu_rate_percent > 100:
            raise ValueError("El límite de CPU debe estar entre 1 y 100")
        if self.max_audit_bytes > self.max_audit_total_bytes:
            raise ValueError("La cuota de un log no puede superar la cuota global")
        if self.cpu_profile not in {"intel-i3-8gb", "intel-i5-8gb"}:
            raise ValueError("Perfil CPU no soportado")
        expected_threads = 4 if self.cpu_profile == "intel-i5-8gb" else 2
        if self.model_threads > expected_threads:
            raise ValueError("Los hilos del modelo superan el perfil CPU seleccionado")
        if self.max_ocr_calls < self.max_ocr_pages + 1:
            raise ValueError("OCR requiere presupuesto para búsqueda más un detalle")
        if self.max_ocr_total_pixels < self.max_ocr_pages * self.max_ocr_search_pixels + self.max_ocr_detail_pixels:
            raise ValueError("El presupuesto total OCR es incoherente")

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


def intel_8gb_policy(processor: str = "i3", **overrides: object) -> SecurityPolicy:
    """Crea el perfil CPU soportado para Windows 10/11 x64.

    ``i3`` queda limitado a dos hilos; ``i5`` permite cuatro hilos. La cuota
    de memoria del Job ya es 2 GiB, la cola tiene un único trabajo activo y
    ningún parámetro permite elevar esos límites por defecto.
    """
    normalized = processor.strip().lower()
    if normalized not in {"i3", "i5"}:
        raise ValueError("El procesador debe ser i3 o i5")
    profile = f"intel-{normalized}-8gb"
    values = {
        "cpu_profile": profile,
        "model_threads": 4 if normalized == "i5" else 2,
        "max_concurrent_jobs": 1,
        "max_queued_jobs": 1,
        "windows_job_memory_bytes": 2 * 1024 * 1024 * 1024,
    }
    values.update(overrides)
    # A profile factory is a security boundary, not a convenience constructor:
    # callers may lower limits but never raise the hardware envelope.
    if values["max_concurrent_jobs"] != 1 or values["max_queued_jobs"] != 1:
        raise ValueError("El perfil CPU solo admite un trabajo activo y uno en espera")
    if int(values["windows_job_memory_bytes"]) > 2 * 1024 * 1024 * 1024:
        raise ValueError("El perfil CPU no puede superar 2 GiB por trabajo")
    if int(values["model_threads"]) > (4 if normalized == "i5" else 2):
        raise ValueError("Los hilos superan la capacidad del procesador seleccionado")
    return SecurityPolicy(**values)


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
