"""HTTP adapter for the local UI.

This module deliberately exposes a very small, loopback-only API. It accepts a
PDF upload, places it in an application-owned job directory, and runs the
existing fail-closed pipeline. No URL, path, shell command, or external
provider is accepted from the client.

Run with::

    PYTHONPATH=src python -m cubicador.http --port 8765
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Full, Queue
from typing import Any
from urllib.parse import urlsplit

from .models import ExtractionResult
from .pipeline import process_pdf
from .runtime import atomic_write_bytes
from .security import DEFAULT_SECURITY_POLICY, SecurityPolicy, SecurityViolation, ensure_within
from .toolchain import _reparse
from .toolchain import ToolchainError, resolve_poppler_for_launch


JOB_TTL_SECONDS = 6 * 60 * 60
MAX_RETAINED_JOBS = 16
MAX_JOB_STORAGE_BYTES = 300 * 1024 * 1024
MAX_REQUEST_THREADS = 4
MAX_UPLOAD_SECONDS = 30


class ApiError(Exception):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST, code: str = "BAD_REQUEST"):
        super().__init__(message)
        self.status, self.code = status, code


@dataclass
class Job:
    job_id: str
    root: Path
    input_path: Path
    created_at: float = field(default_factory=time.time)
    status: str = "queued"
    result: ExtractionResult | None = None
    error: str | None = None
    cancel_requested: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)


class JobManager:
    """Single-worker, bounded local queue with application-owned files."""

    def __init__(self, root: Path, policy: SecurityPolicy = DEFAULT_SECURITY_POLICY):
        self.policy = policy
        raw_root = Path(root)
        if _has_link_component(raw_root):
            raise SecurityViolation("La carpeta de trabajos no puede ser un enlace")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.queue: Queue[Job] = Queue(maxsize=policy.max_queued_jobs)
        self.jobs: dict[str, Job] = {}
        self.lock = threading.RLock()
        try:
            resolve_poppler_for_launch("pdftotext", developer_mode=policy.developer_tools_enabled)
            self.text_ready = True
        except (ToolchainError, FileNotFoundError):
            self.text_ready = False
        self.worker = threading.Thread(target=self._worker, name="cubicador-local-worker", daemon=True)
        self.worker.start()
        self.reaper = threading.Thread(target=self._reaper, name="cubicador-local-reaper", daemon=True)
        self.reaper.start()

    def _reaper(self) -> None:
        while True:
            time.sleep(60)
            with self.lock:
                self._prune_locked()

    def _prune_locked(self) -> None:
        now = time.time()
        candidates = [job for job in self.jobs.values() if job.status in {"completed", "failed", "cancelled"}]
        total = _directory_size(self.root)
        victims = [job for job in candidates if now - job.created_at > JOB_TTL_SECONDS]
        victims.extend(sorted((job for job in candidates if job not in victims), key=lambda item: item.created_at)[:max(0, len(self.jobs) - MAX_RETAINED_JOBS)])
        for job in victims:
            self._remove_locked(job)
            total = _directory_size(self.root)
        if total > MAX_JOB_STORAGE_BYTES:
            for job in sorted((item for item in candidates if item.job_id in self.jobs), key=lambda item: item.created_at):
                self._remove_locked(job)
                total = _directory_size(self.root)
                if total <= MAX_JOB_STORAGE_BYTES:
                    break

    def _remove_locked(self, job: Job) -> None:
        # Only delete a validated, terminal, application-owned job directory.
        if job.status not in {"completed", "failed", "cancelled"}:
            return
        if _reparse(job.root):
            return
        target = ensure_within(job.root, self.root)
        if target.parent != self.root or target.name != job.job_id:
            return
        shutil.rmtree(target, ignore_errors=True)
        self.jobs.pop(job.job_id, None)

    def submit(self, content: bytes, filename: str, *, ocr: bool = False) -> Job:
        with self.lock:
            self._prune_locked()
            if _directory_size(self.root) + len(content) > MAX_JOB_STORAGE_BYTES:
                raise ApiError("El almacenamiento local de trabajos está lleno", HTTPStatus.INSUFFICIENT_STORAGE, "STORAGE_FULL")
        if len(content) == 0 or len(content) > self.policy.max_pdf_bytes:
            raise ApiError("El PDF excede el tamaño máximo permitido", HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "PDF_TOO_LARGE")
        if not filename.lower().endswith(".pdf") or content[:5] != b"%PDF-":
            raise ApiError("La entrada debe ser un PDF válido", HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "INVALID_PDF")
        # OCR is opt-in, but cannot be enabled by clients until the signed bundle is present.
        if ocr:
            raise ApiError("OCR local no está disponible en este bundle", HTTPStatus.NOT_IMPLEMENTED, "OCR_NOT_READY")
        job_id = uuid.uuid4().hex
        job_root = ensure_within(self.root / job_id, self.root)
        job_root.mkdir(mode=0o700)
        input_path = job_root / "input.pdf"
        with input_path.open("xb") as stream:
            stream.write(content)
        job = Job(job_id, job_root, input_path)
        with self.lock:
            self.jobs[job_id] = job
        try:
            self.queue.put_nowait(job)
        except Full as exc:
            with self.lock:
                self.jobs.pop(job_id, None)
            shutil.rmtree(job_root, ignore_errors=True)
            raise ApiError("La cola de trabajos está completa", HTTPStatus.TOO_MANY_REQUESTS, "QUEUE_FULL") from exc
        return job

    def _worker(self) -> None:
        while True:
            try:
                job = self.queue.get()
            except Exception:  # pragma: no cover - Queue.get should not fail
                continue
            try:
                with self.lock:
                    if job.cancel_requested:
                        job.status = "cancelled"
                        continue
                    job.status = "running"
                try:
                    xlsx = job.root / "cantidades.xlsx"
                    text = job.root / "layout.txt"
                    result = process_pdf(job.input_path, xlsx, text_path=text, output_root=job.root,
                                         policy=self.policy, cancel=job.cancel_event)
                    with self.lock:
                        if job.cancel_requested:
                            _cleanup_outputs(job)
                            job.status = "cancelled"
                            job.error = "Cancelación solicitada; se descartó el resultado"
                        else:
                            job.result = result
                            job.status = "completed"
                            atomic_write_bytes(job.root / "result.json", (result.model_dump_json(indent=2) + "\n").encode())
                except Exception as exc:
                    with self.lock:
                        if job.cancel_requested:
                            _cleanup_outputs(job)
                        job.error = _public_error(exc)
                        job.status = "cancelled" if job.cancel_requested else "failed"
            finally:
                self.queue.task_done()

    def get(self, job_id: str) -> Job:
        if not _valid_job_id(job_id):
            raise ApiError("Identificador de trabajo inválido", HTTPStatus.NOT_FOUND, "NOT_FOUND")
        with self.lock:
            job = self.jobs.get(job_id)
        if job is None:
            raise ApiError("Trabajo no encontrado", HTTPStatus.NOT_FOUND, "NOT_FOUND")
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        with self.lock:
            if job.status in {"completed", "failed", "cancelled"}:
                return job
            job.cancel_requested = True
            job.cancel_event.set()
            if job.status == "queued":
                job.status = "cancelled"
        return job

    def status(self, job: Job) -> dict[str, Any]:
        with self.lock:
            result = job.result.model_dump(mode="json") if job.result else None
            payload: dict[str, Any] = {
                "job_id": job.job_id,
                "status": job.status,
                "created_at": job.created_at,
                "error": job.error,
                "cancel_requested": job.cancel_requested,
                "result": result,
            }
            if job.status == "completed":
                payload["download_url"] = f"/jobs/{job.job_id}/excel"
            return payload


def _public_error(exc: Exception) -> str:
    if isinstance(exc, (SecurityViolation, ValueError)):
        return str(exc)[:240]
    # Do not expose paths, tracebacks, or tool output through the local API.
    return "No se pudo procesar el PDF; revise el estado y los pendientes"


def _valid_job_id(value: str) -> bool:
    return len(value) == 32 and all(character in "0123456789abcdef" for character in value)


def _cleanup_outputs(job: Job) -> None:
    for name in ("cantidades.xlsx", "layout.txt", "result.json"):
        target = ensure_within(job.root / name, job.root)
        if target.is_file() and not _reparse(target):
            target.unlink()


def _directory_size(root: Path) -> int:
    total = 0
    if not root.exists() or _reparse(root):
        return 0
    for path in root.rglob("*"):
        if path.is_file() and not _reparse(path):
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _has_link_component(path: Path) -> bool:
    candidate = path.absolute()
    current = Path(candidate.anchor) if candidate.anchor else Path.cwd().anchor and Path(Path.cwd().anchor)
    for part in candidate.parts[1:] if candidate.anchor else candidate.parts:
        current = current / part
        if current.exists() and _reparse(current):
            return True
    return False


def _read_multipart(stream: Any, headers: Any, max_pdf_bytes: int) -> tuple[bytes, str, bool]:
    """Read only a bounded multipart body into memory; never uses global temp files."""
    raw_length = headers.get("Content-Length")
    if raw_length is None or not raw_length.isdigit():
        raise ApiError("Solicitud sin longitud válida", HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "REQUEST_TOO_LARGE")
    length = int(raw_length)
    if length <= 0 or length > max_pdf_bytes + 2 * 1024 * 1024:
        raise ApiError("Solicitud demasiado grande", HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "REQUEST_TOO_LARGE")
    content_type = headers.get("Content-Type", "")
    boundary_match = re.search(r"boundary=(?:\"([^\"]+)\"|([^;\s]+))", content_type, re.IGNORECASE)
    if not boundary_match:
        raise ApiError("Multipart sin boundary válido", HTTPStatus.BAD_REQUEST, "MULTIPART_INVALID")
    boundary = (boundary_match.group(1) or boundary_match.group(2)).encode("ascii", "strict")
    if len(boundary) > 200 or b"\r" in boundary or b"\n" in boundary:
        raise ApiError("Boundary multipart inválido", HTTPStatus.BAD_REQUEST, "MULTIPART_INVALID")
    body = stream.read(length)
    if len(body) != length:
        raise ApiError("Solicitud incompleta", HTTPStatus.BAD_REQUEST, "MULTIPART_INCOMPLETE")
    pdf: bytes | None = None
    filename = "documento.pdf"
    ocr = False
    seen: set[str] = set()
    marker = b"--" + boundary
    for chunk in body.split(marker)[1:]:
        if chunk.startswith(b"--"):
            break
        chunk = chunk.lstrip(b"\r\n")
        head, separator, data = chunk.partition(b"\r\n\r\n")
        if not separator:
            continue
        if data.endswith(b"\r\n"):
            data = data[:-2]
        disposition = next((line for line in head.decode("latin-1", "replace").split("\r\n")
                            if line.lower().startswith("content-disposition:")), "")
        field = re.search(r"(?:^|;)\s*name=\"?([^\";]+)", disposition, re.IGNORECASE)
        if not field:
            continue
        name = field.group(1)
        if name not in {"pdf", "ocr"} or name in seen:
            raise ApiError("Campos multipart no permitidos o repetidos", HTTPStatus.BAD_REQUEST, "MULTIPART_FIELDS")
        seen.add(name)
        if name == "pdf":
            file_match = re.search(r"filename=\"([^\"]*)\"", disposition, re.IGNORECASE)
            filename = os.path.basename(file_match.group(1)) if file_match else filename
            pdf = data
        elif name == "ocr":
            ocr = data.decode("utf-8", "replace").strip().lower() == "true"
    if pdf is None:
        raise ApiError("Falta el campo PDF", HTTPStatus.BAD_REQUEST, "PDF_REQUIRED")
    return pdf, filename, ocr


class LocalApiHandler(BaseHTTPRequestHandler):
    manager: JobManager

    server_version = "CubicadorLocal/1"

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(MAX_UPLOAD_SECONDS)

    def log_message(self, format: str, *args: object) -> None:  # pragma: no cover - avoid leaking filenames
        return

    def _send_json(self, payload: object, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, exc: ApiError) -> None:
        self._send_json({"error": str(exc), "code": exc.code}, exc.status)

    def _authenticate(self) -> None:
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://127.0.0.1:3000", "http://localhost:3000"}:
            raise ApiError("Origen no permitido", HTTPStatus.FORBIDDEN, "ORIGIN_DENIED")
        token = self.headers.get("Authorization", "")
        if token != f"Bearer {self.server.api_token}":
            raise ApiError("Token local inválido", HTTPStatus.UNAUTHORIZED, "UNAUTHORIZED")

    def do_OPTIONS(self) -> None:
        try:
            self._authenticate()
        except ApiError as exc:
            self._error(exc)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET,POST,DELETE,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        try:
            self._authenticate()
            path = urlsplit(self.path).path
            if path == "/health":
                with self.manager.lock:
                    active = sum(job.status == "running" for job in self.manager.jobs.values())
                    queued = sum(job.status == "queued" for job in self.manager.jobs.values())
                self._send_json({"status": "ok", "offline": True, "active": active, "queued": queued,
                                 "capabilities": {"text": self.manager.text_ready, "ocr": False}})
                return
            parts = path.strip("/").split("/")
            if len(parts) == 2 and parts[0] == "jobs":
                self._send_json(self.manager.status(self.manager.get(parts[1])))
                return
            if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "excel":
                job = self.manager.get(parts[1])
                if job.status != "completed":
                    raise ApiError("El resultado todavía no está disponible", HTTPStatus.CONFLICT, "NOT_READY")
                target = ensure_within(job.root / "cantidades.xlsx", job.root)
                if _reparse(target) or not target.is_file():
                    raise ApiError("No existe el Excel del trabajo", HTTPStatus.GONE, "OUTPUT_MISSING")
                body = target.read_bytes()
                if len(body) > self.manager.policy.max_output_bytes:
                    raise ApiError("El resultado excede la cuota", HTTPStatus.INTERNAL_SERVER_ERROR, "OUTPUT_TOO_LARGE")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", f'attachment; filename="cantidades-{job.job_id[:8]}.xlsx"')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            raise ApiError("Ruta no encontrada", HTTPStatus.NOT_FOUND, "NOT_FOUND")
        except ApiError as exc:
            self._error(exc)

    def do_DELETE(self) -> None:
        try:
            self._authenticate()
            parts = urlsplit(self.path).path.strip("/").split("/")
            if len(parts) != 2 or parts[0] != "jobs":
                raise ApiError("Ruta no encontrada", HTTPStatus.NOT_FOUND, "NOT_FOUND")
            self._send_json(self.manager.status(self.manager.cancel(parts[1])))
        except ApiError as exc:
            self._error(exc)

    def do_POST(self) -> None:
        try:
            self._authenticate()
            path = urlsplit(self.path).path
            if path != "/jobs":
                raise ApiError("Ruta no encontrada", HTTPStatus.NOT_FOUND, "NOT_FOUND")
            raw_length = self.headers.get("Content-Length")
            if raw_length is None or not raw_length.isdigit() or int(raw_length) > self.manager.policy.max_pdf_bytes + 2 * 1024 * 1024:
                raise ApiError("Solicitud demasiado grande o sin longitud", HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "REQUEST_TOO_LARGE")
            content_type = self.headers.get("Content-Type", "")
            if not content_type.lower().startswith("multipart/form-data"):
                raise ApiError("Se requiere multipart/form-data", HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "MULTIPART_REQUIRED")
            content, filename, ocr = _read_multipart(self.rfile, self.headers, self.manager.policy.max_pdf_bytes)
            job = self.manager.submit(content, filename, ocr=ocr)
            self._send_json(self.manager.status(job), HTTPStatus.ACCEPTED)
        except ApiError as exc:
            self._error(exc)
        except (SecurityViolation, OSError, ValueError) as exc:
            self._error(ApiError(_public_error(exc), HTTPStatus.BAD_REQUEST, "INVALID_REQUEST"))


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server with a finite request budget."""

    def __init__(self, server_address: tuple[str, int], handler_class: type[BaseHTTPRequestHandler], *, api_token: str):
        super().__init__(server_address, handler_class)
        self.request_slots = threading.BoundedSemaphore(MAX_REQUEST_THREADS)
        self.api_token = api_token
        self.daemon_threads = True

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self.request_slots.acquire(blocking=False):
            request.close()
            return
        thread = threading.Thread(target=self.process_request_thread, args=(request, client_address), daemon=True)
        thread.start()

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()


def create_server(host: str = "127.0.0.1", port: int = 8765, *, root: str | Path = "./.cubicador-api",
                  policy: SecurityPolicy = DEFAULT_SECURITY_POLICY, token: str | None = None) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise SecurityViolation("El servidor local solo puede escuchar en 127.0.0.1")
    manager = JobManager(Path(root), policy)
    api_token = token or os.environ.get("CUBICADOR_API_TOKEN")
    if not api_token:
        raise SecurityViolation("Debe proporcionar un token efímero mediante CUBICADOR_API_TOKEN")
    if len(api_token) < 32:
        raise SecurityViolation("El token local debe tener al menos 32 caracteres")
    server = BoundedThreadingHTTPServer((host, port), LocalApiHandler, api_token=api_token)
    server.api_token = api_token
    server.manager = manager
    server.RequestHandlerClass.manager = manager
    return server


def main() -> int:
    parser = argparse.ArgumentParser(description="API local offline de Cubicador")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", default="./.cubicador-api")
    parser.add_argument("--cpu-profile", choices=("i3", "i5"), default="i3")
    args = parser.parse_args()
    from .security import intel_8gb_policy
    server = create_server(args.host, args.port, root=args.root,
                           policy=intel_8gb_policy(args.cpu_profile))
    print(f"Cubicador API local escuchando en http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
