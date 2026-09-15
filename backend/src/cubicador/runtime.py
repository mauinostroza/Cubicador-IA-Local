from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import threading
import time
import signal

from .security import DEFAULT_SECURITY_POLICY, SecurityPolicy, SecurityViolation, ensure_within


class CancelledError(RuntimeError):
    pass


class JobWorkspace(AbstractContextManager[Path]):
    """Directorio privado, temporal y siempre limpiado para un trabajo."""

    def __init__(self, policy: SecurityPolicy = DEFAULT_SECURITY_POLICY):
        self.policy = policy
        self.path: Path | None = None

    def __enter__(self) -> Path:
        self.path = Path(tempfile.mkdtemp(prefix="cubicador-"))
        return self.path

    def check_quota(self) -> None:
        assert self.path is not None
        total = sum(p.stat().st_size for p in self.path.rglob("*") if p.is_file() and not p.is_symlink())
        if total > self.policy.max_temp_bytes:
            raise SecurityViolation("El trabajo excedió la cuota temporal")

    def __exit__(self, *_: object) -> None:
        if self.path is not None:
            shutil.rmtree(self.path, ignore_errors=True)


class JobGate:
    """Límite fail-fast de trabajos activos más trabajos en espera."""

    def __init__(self, policy: SecurityPolicy = DEFAULT_SECURITY_POLICY):
        self._run = threading.BoundedSemaphore(policy.max_concurrent_jobs)
        self._slots = threading.BoundedSemaphore(policy.max_concurrent_jobs + policy.max_queued_jobs)

    def __enter__(self) -> "JobGate":
        if not self._slots.acquire(blocking=False):
            raise SecurityViolation("Cola de trabajos completa")
        self._run.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self._run.release()
        self._slots.release()


def run_command(
    argv: list[str], *, policy: SecurityPolicy = DEFAULT_SECURITY_POLICY,
    timeout: int | None = None, cancel: threading.Event | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if not argv or not all(isinstance(value, str) and "\x00" not in value for value in argv):
        raise SecurityViolation("Comando inválido")
    executable = Path(argv[0])
    if not executable.is_absolute() or not executable.resolve(strict=True).is_file():
        raise SecurityViolation("El ejecutable debe ser una ruta absoluta existente")
    if cancel and cancel.is_set():
        raise CancelledError("Trabajo cancelado")
    if os.name == "nt":
        raise SecurityViolation("Subprocesos deshabilitados en Windows hasta activar Job Objects")
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=stdout_file, stderr=stderr_file,
            shell=False, start_new_session=True, env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )
        deadline = time.monotonic() + (timeout or policy.subprocess_timeout_seconds)
        failure: Exception | None = None
        while process.poll() is None:
            output_size = os.fstat(stdout_file.fileno()).st_size + os.fstat(stderr_file.fileno()).st_size
            if output_size > policy.max_process_output_bytes:
                failure = SecurityViolation("El proceso excedió el límite de salida")
            elif cancel and cancel.is_set():
                failure = CancelledError("Trabajo cancelado")
            elif time.monotonic() >= deadline:
                failure = TimeoutError("El proceso excedió el tiempo permitido")
            if failure:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise failure
            time.sleep(0.02)
        final_size = os.fstat(stdout_file.fileno()).st_size + os.fstat(stderr_file.fileno()).st_size
        if final_size > policy.max_process_output_bytes:
            raise SecurityViolation("El proceso excedió el límite de salida")
        stdout_file.seek(0); stderr_file.seek(0)
        stdout, stderr = stdout_file.read(), stderr_file.read()
    if len(stdout) + len(stderr) > policy.max_process_output_bytes:
        raise SecurityViolation("El proceso excedió el límite de salida")
    if cancel and cancel.is_set():
        raise CancelledError("Trabajo cancelado")
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


def safe_output_path(path: str | Path, output_root: str | Path, suffix: str) -> Path:
    candidate = ensure_within(path, output_root)
    if candidate.suffix.lower() != suffix:
        raise SecurityViolation(f"La salida debe tener extensión {suffix}")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    if candidate.exists() and candidate.is_symlink():
        raise SecurityViolation("No se admite escribir sobre enlaces simbólicos")
    return candidate


def atomic_write_bytes(path: Path, data: bytes, policy: SecurityPolicy = DEFAULT_SECURITY_POLICY) -> None:
    if len(data) > policy.max_output_bytes:
        raise SecurityViolation("La salida excede el tamaño permitido")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
