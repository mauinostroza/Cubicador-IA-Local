from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import asdict
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import signal
import uuid

from .security import DEFAULT_SECURITY_POLICY, SecurityPolicy, SecurityViolation, ensure_within


class CancelledError(RuntimeError):
    pass


_WINDOWS_CREATE_LOCK = threading.Lock()


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


class _AuditLock:
    def __init__(self, path: Path):
        self.path, self.file = path, None

    def __enter__(self):
        self.file = self.path.open("a+b")
        if self.file.tell() == 0:
            self.file.write(b"0"); self.file.flush()
        if os.name == "nt":
            import msvcrt
            self.file.seek(0); msvcrt.locking(self.file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        assert self.file is not None
        if os.name == "nt":
            import msvcrt
            self.file.seek(0); msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        self.file.close()


class AuditStore:
    """Almacén con retención/cuota global y un log encadenado por trabajo."""

    def __init__(self, root: str | Path, policy: SecurityPolicy = DEFAULT_SECURITY_POLICY):
        self.root = ensure_within(Path(root) / ".cubicador-audit", root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.policy = policy
        self.lock_path = self.root / ".lock"

    def start_job(self) -> "AuditLog":
        with _AuditLock(self.lock_path):
            files = sorted(self.root.glob("*.jsonl"), key=lambda p: p.stat().st_mtime_ns)
            total = sum(p.stat().st_size for p in files)
            now = time.time()
            while files and (len(files) >= self.policy.max_audit_files or total >= self.policy.max_audit_total_bytes):
                victim = next((path for path in files if self._is_purgeable(path, now)), None)
                if victim is None:
                    raise SecurityViolation("Retención llena con trabajos de auditoría activos")
                files.remove(victim); total -= victim.stat().st_size; victim.unlink()
            job_id = uuid.uuid4().hex
            path = self.root / f"{job_id}.jsonl"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
        return AuditLog(self, path, job_id)

    def _is_purgeable(self, path: Path, now: float) -> bool:
        """Terminal, o abandonado: un proceso muerto por SIGKILL/OOM nunca llega a escribir su evento terminal."""
        if self._is_terminal(path):
            return True
        try:
            return (now - path.stat().st_mtime) > self.policy.max_audit_orphan_seconds
        except OSError:
            return False

    @staticmethod
    def _is_terminal(path: Path) -> bool:
        try:
            lines = path.read_bytes().splitlines()
            return bool(lines) and json.loads(lines[-1])["event"] in AuditLog._TERMINAL
        except (OSError, ValueError, KeyError, IndexError):
            return False

    def verify(self, path: str | Path) -> bool:
        candidate = ensure_within(path, self.root)
        previous, expected_seq, policy_hash = "0" * 64, 1, None
        for raw in candidate.read_bytes().splitlines():
            record = json.loads(raw)
            event_hash = record.pop("event_hash")
            canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            if (record.get("schema") != 1 or record.get("seq") != expected_seq or
                    record.get("previous_hash") != previous or hashlib.sha256(canonical).hexdigest() != event_hash):
                return False
            policy_hash = policy_hash or record.get("policy_hash")
            if record.get("policy_hash") != policy_hash:
                return False
            previous, expected_seq = event_hash, expected_seq + 1
        return expected_seq > 1


class AuditLog:
    _ALLOWED_KEYS = {"duration_ms", "bytes", "exit_code", "detail_code"}
    _TERMINAL = {"job_completed", "job_failed"}

    def __init__(self, store: AuditStore, path: Path, job_id: str):
        self.store, self.path, self.job_id, self.policy = store, path, job_id, store.policy
        policy_json = json.dumps(asdict(self.policy), sort_keys=True, separators=(",", ":"), default=list).encode()
        self.policy_hash = hashlib.sha256(policy_json).hexdigest()
        self._terminal = False
        self._seq = 0
        self._previous_hash = "0" * 64

    def append(self, event: str, status: str, **metrics: int | str) -> None:
        if self._terminal:
            raise SecurityViolation("El log ya posee un evento terminal")
        if not event.isidentifier() or not status.isidentifier() or set(metrics) - self._ALLOWED_KEYS:
            raise SecurityViolation("Campo de auditoría no permitido")
        if any(isinstance(value, str) and len(value) > 64 for value in metrics.values()):
            raise SecurityViolation("Valor de auditoría demasiado largo")
        self._seq += 1
        record = {"schema": 1, "seq": self._seq, "timestamp": datetime.now(timezone.utc).isoformat(),
            "job_id": self.job_id, "policy_hash": self.policy_hash, "previous_hash": self._previous_hash,
            "event": event, "status": status, **metrics}
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        record["event_hash"] = hashlib.sha256(canonical).hexdigest()
        encoded = (json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        with _AuditLock(self.store.lock_path):
            current = self.path.stat().st_size
            total = sum(p.stat().st_size for p in self.store.root.glob("*.jsonl"))
            if current + len(encoded) > self.policy.max_audit_bytes or total + len(encoded) > self.policy.max_audit_total_bytes:
                raise SecurityViolation("La auditoría excedió su cuota")
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND)
            try:
                os.write(fd, encoded); os.fsync(fd)
            finally:
                os.close(fd)
        self._previous_hash = record["event_hash"]
        self._terminal = event in self._TERMINAL


def run_command(
    argv: list[str], *, policy: SecurityPolicy = DEFAULT_SECURITY_POLICY,
    workspace: str | Path | None, timeout: int | None = None, cancel: threading.Event | None = None,
    audit: AuditLog | None = None,
) -> subprocess.CompletedProcess[bytes]:
    if not argv or not all(isinstance(value, str) and "\x00" not in value for value in argv):
        raise SecurityViolation("Comando inválido")
    executable = Path(argv[0])
    if not executable.is_absolute() or not executable.resolve(strict=True).is_file():
        raise SecurityViolation("El ejecutable debe ser una ruta absoluta existente")
    if cancel and cancel.is_set():
        raise CancelledError("Trabajo cancelado")
    if workspace is None:
        raise SecurityViolation("workspace es obligatorio para ejecutar procesos")
    workdir = Path(workspace)
    if workdir.is_symlink() or not workdir.resolve(strict=True).is_dir():
        raise SecurityViolation("workspace inválido")
    if _is_windows():
        if not policy.windows_job_objects_enabled:
            raise SecurityViolation("Job Objects Windows no habilitados: falta smoke/CI aprobado")
        return _run_windows_job(argv, policy, timeout or policy.subprocess_timeout_seconds, cancel, workdir, audit)
    with tempfile.TemporaryFile(dir=workdir) as stdout_file, tempfile.TemporaryFile(dir=workdir) as stderr_file:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=stdout_file, stderr=stderr_file,
            shell=False, start_new_session=True, cwd=workdir, env={"PATH": os.defpath, "LANG": "C.UTF-8"},
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


def _is_windows() -> bool:
    return os.name == "nt"


def _run_windows_job(argv: list[str], policy: SecurityPolicy, timeout: int, cancel: threading.Event | None, workspace: Path, audit: AuditLog | None = None) -> subprocess.CompletedProcess[bytes]:
    """CreateProcessW suspendido y asignado a un Job antes de ejecutar una instrucción."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    INVALID = wintypes.HANDLE(-1).value
    CREATE_SUSPENDED, CREATE_NO_WINDOW, CREATE_UNICODE_ENVIRONMENT, EXTENDED_STARTUPINFO_PRESENT = 0x00000004, 0x08000000, 0x00000400, 0x00080000
    STARTF_USESTDHANDLES = 0x00000100
    WAIT_OBJECT_0, WAIT_TIMEOUT, WAIT_FAILED = 0, 258, 0xFFFFFFFF
    PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
    JOB_OBJECT_LIMIT_JOB_TIME = 0x4
    JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
    JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JOB_OBJECT_CPU_RATE_CONTROL_ENABLE = 0x1
    JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP = 0x4

    class SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", wintypes.LPVOID), ("bInheritHandle", wintypes.BOOL)]

    class STARTUPINFO(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                    ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD), ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
                    ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.POINTER(ctypes.c_byte)), ("hStdInput", wintypes.HANDLE),
                    ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE), ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    class STARTUPINFOEX(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", wintypes.LPVOID)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMITS(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED_LIMITS(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC_LIMITS), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    class CPU_RATE(ctypes.Structure):
        _fields_ = [("ControlFlags", wintypes.DWORD), ("CpuRate", wintypes.DWORD)]

    class BASIC_ACCOUNTING(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                    ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                    ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
                    ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD)]

    kernel32.CreateJobObjectW.argtypes = [ctypes.POINTER(SECURITY_ATTRIBUTES), wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.LPVOID, wintypes.LPVOID, wintypes.BOOL,
        wintypes.DWORD, wintypes.LPVOID, wintypes.LPCWSTR, wintypes.LPVOID, ctypes.POINTER(PROCESS_INFORMATION)]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel32.ResumeThread.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID]
    kernel32.InitializeProcThreadAttributeList.argtypes = [wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel32.UpdateProcThreadAttribute.argtypes = [wintypes.LPVOID, wintypes.DWORD, ctypes.c_size_t, wintypes.LPVOID, ctypes.c_size_t, wintypes.LPVOID, wintypes.LPVOID]
    kernel32.DeleteProcThreadAttributeList.argtypes = [wintypes.LPVOID]

    def check(ok: object, operation: str) -> None:
        if not ok:
            raise OSError(ctypes.get_last_error(), f"Windows Job Object: {operation}")

    def terminate_and_verify(job_handle, process_handle) -> None:
        check(kernel32.TerminateJobObject(job_handle, 1), "terminación")
        if audit:
            audit.append("termination_requested", "ok")
        deadline = time.monotonic() + policy.termination_grace_seconds
        while time.monotonic() < deadline:
            accounting = BASIC_ACCOUNTING()
            check(kernel32.QueryInformationJobObject(job_handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None), "verificación")
            if kernel32.WaitForSingleObject(process_handle, 0) == WAIT_OBJECT_0 and accounting.ActiveProcesses == 0:
                if audit:
                    audit.append("termination_verified", "ok")
                return
            time.sleep(0.02)
        raise SecurityViolation("No se pudo verificar la terminación completa del Job")

    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        raise OSError(ctypes.get_last_error(), "No se pudo crear el Job Object")
    process_info = PROCESS_INFORMATION()
    created = False
    assigned = False
    attribute_buffer = None
    with tempfile.TemporaryFile(dir=workspace) as stdin_file, tempfile.TemporaryFile(dir=workspace) as stdout_file, tempfile.TemporaryFile(dir=workspace) as stderr_file:
        try:
            limits = EXTENDED_LIMITS()
            limits.BasicLimitInformation.PerJobUserTimeLimit = timeout * 10_000_000
            limits.BasicLimitInformation.ActiveProcessLimit = policy.windows_active_process_limit
            limits.BasicLimitInformation.LimitFlags = (JOB_OBJECT_LIMIT_JOB_TIME | JOB_OBJECT_LIMIT_ACTIVE_PROCESS |
                JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_JOB_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE)
            limits.ProcessMemoryLimit = policy.windows_process_memory_bytes
            limits.JobMemoryLimit = policy.windows_job_memory_bytes
            check(kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)), "límites")
            cpu = CPU_RATE(JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP, policy.windows_cpu_rate_percent * 100)
            check(kernel32.SetInformationJobObject(job, 15, ctypes.byref(cpu), ctypes.sizeof(cpu)), "límite CPU")

            std_handles = tuple(msvcrt.get_osfhandle(stream.fileno()) for stream in (stdin_file, stdout_file, stderr_file))
            startup = STARTUPINFOEX()
            startup.StartupInfo = STARTUPINFO(cb=ctypes.sizeof(STARTUPINFOEX), dwFlags=STARTF_USESTDHANDLES,
                hStdInput=msvcrt.get_osfhandle(stdin_file.fileno()), hStdOutput=msvcrt.get_osfhandle(stdout_file.fileno()), hStdError=msvcrt.get_osfhandle(stderr_file.fileno()))
            attribute_size = ctypes.c_size_t()
            kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attribute_size))
            if not attribute_size.value:
                raise OSError(ctypes.get_last_error(), "No se pudo dimensionar HANDLE_LIST")
            attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
            startup.lpAttributeList = ctypes.cast(attribute_buffer, wintypes.LPVOID)
            check(kernel32.InitializeProcThreadAttributeList(startup.lpAttributeList, 1, 0, ctypes.byref(attribute_size)), "STARTUPINFOEX")
            inherited_handles = (wintypes.HANDLE * 3)(startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError)
            check(kernel32.UpdateProcThreadAttribute(startup.lpAttributeList, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                ctypes.cast(inherited_handles, wintypes.LPVOID), ctypes.sizeof(inherited_handles), None, None), "HANDLE_LIST")
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            safe_environment = {
                "PATH": os.defpath,
                "SYSTEMROOT": os.environ.get("SystemRoot", r"C:\Windows"),
                "WINDIR": os.environ.get("SystemRoot", r"C:\Windows"),
            }
            environment = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(safe_environment.items())) + "\0\0")
            with _WINDOWS_CREATE_LOCK:
                try:
                    for handle in std_handles:
                        os.set_handle_inheritable(handle, True)
                    check(kernel32.CreateProcessW(argv[0], command, None, None, True, CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT,
                        environment, str(workspace), ctypes.byref(startup), ctypes.byref(process_info)), "CreateProcessW")
                    created = True
                finally:
                    for handle in std_handles:
                        os.set_handle_inheritable(handle, False)
            if audit:
                audit.append("process_created_suspended", "ok")
            check(kernel32.AssignProcessToJobObject(job, process_info.hProcess), "asignación")
            assigned = True
            if audit:
                audit.append("process_assigned", "ok")
            if kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
                raise OSError(ctypes.get_last_error(), "No se pudo reanudar el proceso")
            if audit:
                audit.append("process_resumed", "ok")
            deadline = time.monotonic() + timeout
            while True:
                wait_state = kernel32.WaitForSingleObject(process_info.hProcess, 20)
                if wait_state == WAIT_OBJECT_0:
                    break
                if wait_state == WAIT_FAILED:
                    raise OSError(ctypes.get_last_error(), "WaitForSingleObject falló")
                if wait_state != WAIT_TIMEOUT:
                    raise OSError(f"Estado inesperado de WaitForSingleObject: {wait_state}")
                size = os.fstat(stdout_file.fileno()).st_size + os.fstat(stderr_file.fileno()).st_size
                if size > policy.max_process_output_bytes or (cancel and cancel.is_set()) or time.monotonic() >= deadline:
                    terminate_and_verify(job, process_info.hProcess)
                    if size > policy.max_process_output_bytes:
                        raise SecurityViolation("El proceso excedió el límite de salida")
                    if cancel and cancel.is_set():
                        raise CancelledError("Trabajo cancelado")
                    raise TimeoutError("El proceso excedió el tiempo permitido")
            exit_code = wintypes.DWORD()
            check(kernel32.GetExitCodeProcess(process_info.hProcess, ctypes.byref(exit_code)), "código de salida")
            final_size = os.fstat(stdout_file.fileno()).st_size + os.fstat(stderr_file.fileno()).st_size
            if final_size > policy.max_process_output_bytes:
                raise SecurityViolation("El proceso excedió el límite de salida")
            stdout_file.seek(0); stderr_file.seek(0)
            result = subprocess.CompletedProcess(argv, exit_code.value, stdout_file.read(), stderr_file.read())
            if audit:
                audit.append("job_process_completed", "ok", exit_code=exit_code.value)
            return result
        except Exception:
            if created:
                if assigned:
                    terminate_and_verify(job, process_info.hProcess)
                else:
                    check(kernel32.TerminateProcess(process_info.hProcess, 1), "terminación previa a asignación")
                    wait_state = kernel32.WaitForSingleObject(process_info.hProcess, int(policy.termination_grace_seconds * 1000))
                    if wait_state != WAIT_OBJECT_0:
                        raise SecurityViolation("No se pudo terminar el proceso suspendido sin asignar")
            if audit:
                audit.append("job_process_failed", "error")
            raise
        finally:
            if attribute_buffer is not None and 'startup' in locals() and startup.lpAttributeList:
                kernel32.DeleteProcThreadAttributeList(startup.lpAttributeList)
            if process_info.hThread:
                kernel32.CloseHandle(process_info.hThread)
            if process_info.hProcess:
                kernel32.CloseHandle(process_info.hProcess)
            kernel32.CloseHandle(job)  # KILL_ON_JOB_CLOSE cierra cualquier descendiente restante.


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
