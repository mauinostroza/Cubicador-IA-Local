"""Lanzamiento Windows con orden de seguridad indivisible.

El backend WinAPI crea el proceso suspendido con token restringido,
SECURITY_CAPABILITIES sin capabilities y HANDLE_LIST. El orquestador verifica
el token real, asigna el Job y recién entonces reanuda el hilo.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import hashlib
import os
import re
import subprocess
from typing import Protocol
from collections.abc import Callable

from .security import DEFAULT_SECURITY_POLICY, SecurityPolicy, SecurityViolation
from .windows_identity import (
    ChildTokenState, CtypesWindowsIdentityBackend, PreparedNativeIdentity,
    prepare_native_identity,
)


@dataclass(frozen=True)
class SuspendedChild:
    process_handle: int
    thread_handle: int
    process_id: int
    job_handle: int
    executable_sha256: str


class SecureLaunchBackend(Protocol):
    def prepare(self, appcontainer_name: str) -> PreparedNativeIdentity: ...
    def create_suspended(self, prepared: PreparedNativeIdentity, argv: tuple[str, ...],
                         cwd: Path, std_handles: tuple[int, int, int], policy: SecurityPolicy,
                         launch_verifier: Callable[[], Path | None]) -> SuspendedChild: ...
    def inspect_child(self, child: SuspendedChild,
                      prepared: PreparedNativeIdentity) -> ChildTokenState: ...
    def assign_to_job(self, child: SuspendedChild) -> None: ...
    def resume(self, child: SuspendedChild) -> None: ...
    def abort(self, child: SuspendedChild | None) -> None: ...
    def close_prepared(self, prepared: PreparedNativeIdentity) -> None: ...


class GateAudit(Protocol):
    def append(self, event: str, status: str, **metrics: int | str) -> None: ...


def validate_suspended_child(state: ChildTokenState) -> None:
    if not isinstance(state, ChildTokenState):
        raise SecurityViolation("No se pudo verificar el token del hijo")
    if (state.is_appcontainer is not True or state.integrity_level != "low"
            or state.elevated is not False
            or state.privileges != ("SeChangeNotifyPrivilege",)
            or type(state.capability_count) is not int or state.capability_count != 0
            or state.appcontainer_sid_matches is not True
            or state.token_restricted is not True):
        raise SecurityViolation("El hijo suspendido no cumple la identidad restringida")


def create_verified_suspended_child(
    backend: SecureLaunchBackend, argv: tuple[str, ...], cwd: Path,
    std_handles: tuple[int, int, int],
    appcontainer_name: str = "CubicadorIA.OcrWorker",
    policy: SecurityPolicy = DEFAULT_SECURITY_POLICY,
    audit: GateAudit | None = None,
    launch_verifier: Callable[[], Path | None] | None = None,
) -> tuple[PreparedNativeIdentity, SuspendedChild]:
    """Crea, verifica, asigna y reanuda; aborta ante cualquier desviación."""
    prepared: PreparedNativeIdentity | None = None
    child: SuspendedChild | None = None
    resumed = False
    try:
        if audit is None or launch_verifier is None:
            raise SecurityViolation("Auditoría y atestación son obligatorias")
        prepared = prepare_native_identity(backend, appcontainer_name)
        child = backend.create_suspended(prepared, argv, cwd, std_handles, policy, launch_verifier)
        if not isinstance(child, SuspendedChild) or not all(
                type(value) is int and value > 0 for value in
                (child.process_handle, child.thread_handle, child.process_id, child.job_handle)):
            raise SecurityViolation("Handles del hijo inválidos")
        if re.fullmatch(r"[0-9a-f]{64}", child.executable_sha256) is None:
            raise SecurityViolation("Hash del ejecutable inválido")
        backend.assign_to_job(child)
        validate_suspended_child(backend.inspect_child(child, prepared))
        # Si escribir el gate-decision falla, la excepción aborta antes de resume.
        audit.append("gate-decision", "approved", detail_code="restricted_identity",
                     worker_pid=child.process_id, executable_sha256=child.executable_sha256,
                     decision="approved", rule_id="restricted-worker-v1")
        backend.resume(child)
        resumed = True
        return prepared, child
    except Exception:
        abort_error: Exception | None = None
        try:
            if child is not None:
                # Rechazo best-effort: nunca incluye el texto de la excepción.
                try:
                    if audit is not None and re.fullmatch(r"[0-9a-f]{64}", child.executable_sha256):
                        audit.append("gate-decision", "rejected", detail_code="pre_resume_failure",
                                     worker_pid=child.process_id,
                                     executable_sha256=child.executable_sha256,
                                     decision="rejected", rule_id="restricted-worker-v1")
                except Exception:
                    pass
                try:
                    backend.abort(child)
                except Exception as exc:
                    abort_error = exc
        finally:
            if prepared is not None:
                backend.close_prepared(prepared)
        if abort_error is not None:
            raise SecurityViolation("Falló el cleanup del worker rechazado") from abort_error
        raise


class CtypesSecureLaunchBackend(CtypesWindowsIdentityBackend):
    """Implementación Win32 del tramo crítico create/verify/assign/resume."""

    def __init__(self) -> None:
        super().__init__()
        c, w = self.ctypes, self.wintypes
        self._owned: dict[int, tuple[object, ...]] = {}
        self._assigned: set[int] = set()
        self.kernel32.InitializeProcThreadAttributeList.argtypes = [w.LPVOID, w.DWORD, w.DWORD,
                                                                    c.POINTER(c.c_size_t)]
        self.kernel32.InitializeProcThreadAttributeList.restype = w.BOOL
        self.kernel32.UpdateProcThreadAttribute.argtypes = [w.LPVOID, w.DWORD, c.c_size_t,
            w.LPVOID, c.c_size_t, w.LPVOID, w.LPVOID]
        self.kernel32.UpdateProcThreadAttribute.restype = w.BOOL
        self.kernel32.DeleteProcThreadAttributeList.argtypes = [w.LPVOID]
        self.kernel32.CreateJobObjectW.argtypes = [w.LPVOID, w.LPCWSTR]
        self.kernel32.CreateJobObjectW.restype = w.HANDLE
        self.kernel32.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, w.LPVOID, w.DWORD]
        self.kernel32.SetInformationJobObject.restype = w.BOOL
        self.kernel32.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        self.kernel32.AssignProcessToJobObject.restype = w.BOOL
        self.kernel32.ResumeThread.argtypes = [w.HANDLE]
        self.kernel32.ResumeThread.restype = w.DWORD
        self.kernel32.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        self.kernel32.TerminateJobObject.restype = w.BOOL
        self.kernel32.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
        self.kernel32.TerminateProcess.restype = w.BOOL
        self.kernel32.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        self.kernel32.WaitForSingleObject.restype = w.DWORD
        self.kernel32.GetExitCodeProcess.argtypes = [w.HANDLE, c.POINTER(w.DWORD)]
        self.kernel32.GetExitCodeProcess.restype = w.BOOL
        self.kernel32.QueryInformationJobObject.argtypes = [w.HANDLE, c.c_int, w.LPVOID,
                                                             w.DWORD, w.LPVOID]
        self.kernel32.QueryInformationJobObject.restype = w.BOOL
        self.advapi32.CreateProcessAsUserW.argtypes = [w.HANDLE, w.LPCWSTR, w.LPWSTR,
            w.LPVOID, w.LPVOID, w.BOOL, w.DWORD, w.LPVOID, w.LPCWSTR, w.LPVOID, w.LPVOID]
        self.advapi32.CreateProcessAsUserW.restype = w.BOOL

    def create_suspended(self, prepared: PreparedNativeIdentity, argv: tuple[str, ...],
                         cwd: Path, std_handles: tuple[int, int, int], policy: SecurityPolicy,
                         launch_verifier: Callable[[], Path | None]) -> SuspendedChild:
        c, w = self.ctypes, self.wintypes
        if (not argv or not Path(argv[0]).is_absolute()
                or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)
                or not cwd.is_absolute() or not cwd.is_dir() or cwd.is_symlink()
                or (getattr(cwd.stat(), "st_file_attributes", 0) & 0x400)
                or len(std_handles) != 3 or not callable(launch_verifier)):
            raise SecurityViolation("Solicitud nativa de lanzamiento inválida")

        class STARTUPINFO(c.Structure):
            _fields_ = [("cb", w.DWORD), ("lpReserved", w.LPWSTR), ("lpDesktop", w.LPWSTR),
                ("lpTitle", w.LPWSTR), ("dwX", w.DWORD), ("dwY", w.DWORD),
                ("dwXSize", w.DWORD), ("dwYSize", w.DWORD), ("dwXCountChars", w.DWORD),
                ("dwYCountChars", w.DWORD), ("dwFillAttribute", w.DWORD),
                ("dwFlags", w.DWORD), ("wShowWindow", w.WORD), ("cbReserved2", w.WORD),
                ("lpReserved2", c.POINTER(c.c_byte)), ("hStdInput", w.HANDLE),
                ("hStdOutput", w.HANDLE), ("hStdError", w.HANDLE)]
        class STARTUPINFOEX(c.Structure):
            _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", w.LPVOID)]
        class PROCESS_INFORMATION(c.Structure):
            _fields_ = [("hProcess", w.HANDLE), ("hThread", w.HANDLE),
                        ("dwProcessId", w.DWORD), ("dwThreadId", w.DWORD)]
        class SID_AND_ATTRIBUTES(c.Structure):
            _fields_ = [("Sid", w.LPVOID), ("Attributes", w.DWORD)]
        class SECURITY_CAPABILITIES(c.Structure):
            _fields_ = [("AppContainerSid", w.LPVOID),
                        ("Capabilities", c.POINTER(SID_AND_ATTRIBUTES)),
                        ("CapabilityCount", w.DWORD), ("Reserved", w.DWORD)]
        class BASIC_LIMITS(c.Structure):
            _fields_ = [("PerProcessUserTimeLimit", c.c_longlong), ("PerJobUserTimeLimit", c.c_longlong),
                ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", c.c_size_t),
                ("MaximumWorkingSetSize", c.c_size_t), ("ActiveProcessLimit", w.DWORD),
                ("Affinity", c.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
        class IO_COUNTERS(c.Structure):
            _fields_ = [(name, c.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class EXTENDED_LIMITS(c.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMITS), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", c.c_size_t), ("JobMemoryLimit", c.c_size_t),
                ("PeakProcessMemoryUsed", c.c_size_t), ("PeakJobMemoryUsed", c.c_size_t)]
        class CPU_RATE(c.Structure):
            _fields_ = [("ControlFlags", w.DWORD), ("CpuRate", w.DWORD)]

        attribute_size = c.c_size_t()
        self.kernel32.InitializeProcThreadAttributeList(None, 2, 0, c.byref(attribute_size))
        if not attribute_size.value:
            raise OSError(c.get_last_error(), "dimensionar attribute list")
        attribute_buffer = c.create_string_buffer(attribute_size.value)
        startup = STARTUPINFOEX()
        startup.StartupInfo.cb = c.sizeof(STARTUPINFOEX)
        startup.StartupInfo.dwFlags = 0x100
        startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = (
            w.HANDLE(value) for value in std_handles)
        startup.lpAttributeList = c.cast(attribute_buffer, w.LPVOID)
        self._check(self.kernel32.InitializeProcThreadAttributeList(startup.lpAttributeList, 2, 0,
                    c.byref(attribute_size)), "InitializeProcThreadAttributeList")
        handles = (w.HANDLE * 3)(*(w.HANDLE(value) for value in std_handles))
        security = SECURITY_CAPABILITIES(w.LPVOID(prepared.appcontainer_sid), None, 0, 0)
        job = None
        info = PROCESS_INFORMATION()
        inherited_set: list[int] = []
        try:
            self._check(self.kernel32.UpdateProcThreadAttribute(startup.lpAttributeList, 0, 0x00020002,
                        c.cast(handles, w.LPVOID), c.sizeof(handles), None, None), "HANDLE_LIST")
            self._check(self.kernel32.UpdateProcThreadAttribute(startup.lpAttributeList, 0, 0x00020009,
                        c.byref(security), c.sizeof(security), None, None), "SECURITY_CAPABILITIES")
            job = self.kernel32.CreateJobObjectW(None, None)
            if not job:
                raise OSError(c.get_last_error(), "CreateJobObjectW")
            limits = EXTENDED_LIMITS()
            limits.BasicLimitInformation.PerJobUserTimeLimit = policy.subprocess_timeout_seconds * 10_000_000
            limits.BasicLimitInformation.LimitFlags = 0x4 | 0x8 | 0x100 | 0x200 | 0x2000
            limits.BasicLimitInformation.ActiveProcessLimit = 1
            limits.ProcessMemoryLimit = policy.windows_process_memory_bytes
            limits.JobMemoryLimit = policy.windows_job_memory_bytes
            self._check(self.kernel32.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)),
                        "SetInformationJobObject")
            cpu = CPU_RATE(0x1 | 0x4, policy.windows_cpu_rate_percent * 100)
            self._check(self.kernel32.SetInformationJobObject(job, 15, c.byref(cpu), c.sizeof(cpu)),
                        "SetInformationJobObject(CPU)")
            command = c.create_unicode_buffer(subprocess.list2cmdline(argv))
            environment = c.create_unicode_buffer("SYSTEMROOT=" + os.environ.get("SystemRoot", r"C:\Windows") + "\0\0")
            # Comparte el mismo candado que el lanzador Job Object existente:
            # ningún CreateProcess puede coincidir con handles temporalmente heredables.
            from .runtime import _WINDOWS_CREATE_LOCK
            with _WINDOWS_CREATE_LOCK:
                try:
                    verified = launch_verifier()
                    if verified is None or Path(verified).resolve(strict=True) != Path(argv[0]).resolve(strict=True):
                        raise SecurityViolation("Atestación no corresponde al ejecutable")
                    hasher = hashlib.sha256()
                    with Path(argv[0]).open("rb") as executable_stream:
                        for chunk in iter(lambda: executable_stream.read(1024 * 1024), b""):
                            hasher.update(chunk)
                    digest = hasher.hexdigest()
                    for handle in std_handles:
                        os.set_handle_inheritable(handle, True); inherited_set.append(handle)
                    flags = 0x4 | 0x80000 | 0x400 | 0x08000000
                    self._check(self.advapi32.CreateProcessAsUserW(w.HANDLE(prepared.primary_token), argv[0], command,
                                None, None, True, flags, environment, str(cwd), c.byref(startup), c.byref(info)),
                                "CreateProcessAsUserW")
                finally:
                    for handle in inherited_set:
                        os.set_handle_inheritable(handle, False)
        except Exception as launch_error:
            cleanup_error: Exception | None = None
            if info.hProcess:
                try:
                    self._check(self.kernel32.TerminateProcess(info.hProcess, 1),
                                "TerminateProcess(create failure)")
                    if self.kernel32.WaitForSingleObject(info.hProcess, 5000) != 0:
                        raise SecurityViolation("No se verificó terminación tras fallo de create")
                except Exception as exc:
                    cleanup_error = exc
                finally:
                    if info.hThread: self.kernel32.CloseHandle(info.hThread)
                    self.kernel32.CloseHandle(info.hProcess)
            if job:
                self.kernel32.CloseHandle(job)
            if cleanup_error is not None:
                raise SecurityViolation("Cleanup incompleto tras CreateProcessAsUserW") from cleanup_error
            raise launch_error
        finally:
            self.kernel32.DeleteProcThreadAttributeList(startup.lpAttributeList)
        child = SuspendedChild(int(info.hProcess or 0), int(info.hThread or 0),
                               int(info.dwProcessId), int(job or 0), digest)
        self._owned[child.process_id] = (child.process_handle, child.thread_handle, child.job_handle)
        return child

    def inspect_child(self, child: SuspendedChild,
                      prepared: PreparedNativeIdentity) -> ChildTokenState:
        w, c = self.wintypes, self.ctypes
        token = w.HANDLE()
        self._check(self.advapi32.OpenProcessToken(w.HANDLE(child.process_handle), self.TOKEN_QUERY,
                    c.byref(token)), "OpenProcessToken(child)")
        try:
            return self.verify_child_token(int(token.value), prepared.appcontainer_sid)
        finally:
            self.kernel32.CloseHandle(token)

    def assign_to_job(self, child: SuspendedChild) -> None:
        self._check(self.kernel32.AssignProcessToJobObject(self.wintypes.HANDLE(child.job_handle),
                    self.wintypes.HANDLE(child.process_handle)), "AssignProcessToJobObject")
        self._assigned.add(child.process_id)

    def resume(self, child: SuspendedChild) -> None:
        previous = self.kernel32.ResumeThread(self.wintypes.HANDLE(child.thread_handle))
        if previous != 1:
            if previous == 0xFFFFFFFF:
                raise OSError(self.ctypes.get_last_error(), "ResumeThread")
            raise SecurityViolation("Conteo de suspensión inesperado antes de ResumeThread")

    def abort(self, child: SuspendedChild | None) -> None:
        if child is None:
            return
        if child.process_id not in self._owned:
            return
        # Reclama propiedad al inicio: incluso si una API falla, ningún segundo
        # cleanup cerrará handles que Windows ya podría haber reutilizado.
        self._owned.pop(child.process_id)
        assigned = child.process_id in self._assigned
        self._assigned.discard(child.process_id)
        c, w = self.ctypes, self.wintypes
        class BASIC_ACCOUNTING(c.Structure):
            _fields_ = [("TotalUserTime", c.c_longlong), ("TotalKernelTime", c.c_longlong),
                ("ThisPeriodTotalUserTime", c.c_longlong), ("ThisPeriodTotalKernelTime", c.c_longlong),
                ("TotalPageFaultCount", w.DWORD), ("TotalProcesses", w.DWORD),
                ("ActiveProcesses", w.DWORD), ("TotalTerminatedProcesses", w.DWORD)]
        try:
            if assigned:
                self._check(self.kernel32.TerminateJobObject(w.HANDLE(child.job_handle), 1),
                            "TerminateJobObject")
            else:
                self._check(self.kernel32.TerminateProcess(w.HANDLE(child.process_handle), 1),
                            "TerminateProcess")
            if self.kernel32.WaitForSingleObject(w.HANDLE(child.process_handle), 5000) != 0:
                raise SecurityViolation("No se verificó la terminación del proceso")
            accounting = BASIC_ACCOUNTING()
            self._check(self.kernel32.QueryInformationJobObject(w.HANDLE(child.job_handle), 1,
                        c.byref(accounting), c.sizeof(accounting), None), "QueryInformationJobObject")
            if assigned and accounting.ActiveProcesses != 0:
                raise SecurityViolation("El Job conserva procesos activos")
        finally:
            for value in (child.thread_handle, child.process_handle, child.job_handle):
                self.kernel32.CloseHandle(w.HANDLE(value))

    def wait_and_close(self, child: SuspendedChild, timeout_ms: int) -> int:
        """Cierre normal verificando proceso y Job sin procesos activos."""
        c, w = self.ctypes, self.wintypes
        if type(timeout_ms) is not int or not 1 <= timeout_ms <= 600_000:
            raise SecurityViolation("Timeout de espera inválido")
        if child.process_id not in self._owned:
            raise SecurityViolation("Worker ya cerrado o no pertenece al lanzador")
        wait = self.kernel32.WaitForSingleObject(w.HANDLE(child.process_handle), timeout_ms)
        if wait == 258:
            self.abort(child)
            raise TimeoutError("Worker excedió el timeout")
        if wait != 0:
            self.abort(child)
            raise OSError(c.get_last_error(), "WaitForSingleObject")
        code = w.DWORD()
        class BASIC_ACCOUNTING(c.Structure):
            _fields_ = [("TotalUserTime", c.c_longlong), ("TotalKernelTime", c.c_longlong),
                ("ThisPeriodTotalUserTime", c.c_longlong), ("ThisPeriodTotalKernelTime", c.c_longlong),
                ("TotalPageFaultCount", w.DWORD), ("TotalProcesses", w.DWORD),
                ("ActiveProcesses", w.DWORD), ("TotalTerminatedProcesses", w.DWORD)]
        try:
            self._check(self.kernel32.GetExitCodeProcess(w.HANDLE(child.process_handle), c.byref(code)),
                        "GetExitCodeProcess")
            accounting = BASIC_ACCOUNTING()
            self._check(self.kernel32.QueryInformationJobObject(w.HANDLE(child.job_handle), 1,
                        c.byref(accounting), c.sizeof(accounting), None), "QueryInformationJobObject")
            if accounting.ActiveProcesses != 0:
                raise SecurityViolation("Job no quedó sin procesos")
        except Exception:
            self.abort(child)
            raise
        # Reclama propiedad antes de cerrar handles para impedir doble cierre.
        self._owned.pop(child.process_id)
        self._assigned.discard(child.process_id)
        for value in (child.thread_handle, child.process_handle, child.job_handle):
            self.kernel32.CloseHandle(w.HANDLE(value))
        return int(code.value)

    def close_prepared(self, prepared: PreparedNativeIdentity) -> None:
        self.close(prepared)
