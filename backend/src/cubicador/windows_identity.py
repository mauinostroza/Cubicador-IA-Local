"""Contrato fail-closed para la identidad y secretos efímeros del worker.

Este módulo no confunde una comprobación con aislamiento real: el backend de
producción permanece deliberadamente sin implementar hasta que el lanzador
AppContainer sea validado en Windows 10 y 11.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import os
import re
import secrets
from typing import Protocol

from .security import SecurityViolation


@dataclass(frozen=True)
class RestrictedIdentityEvidence:
    container: str
    integrity_level: str
    elevated: bool
    privileges: tuple[str, ...]
    capabilities: tuple[str, ...]
    credential_access: bool


@dataclass(frozen=True)
class PreparedTokenState:
    """Estado previo al hijo; no es evidencia aprobable del gate."""
    integrity_level: str
    elevated: bool
    privileges: tuple[str, ...]
    container_status: str = "pending"
    capabilities_status: str = "unknown"
    credential_access_status: str = "unknown"


@dataclass(frozen=True)
class ChildTokenState:
    """Hechos observados del hijo; no acredita acceso a credenciales."""
    is_appcontainer: bool
    integrity_level: str
    elevated: bool
    privileges: tuple[str, ...]
    capability_count: int
    credential_access_status: str = "unknown"


def validate_restricted_identity(value: RestrictedIdentityEvidence) -> None:
    """Acepta exclusivamente la identidad definida por security-gate.json."""
    if not isinstance(value, RestrictedIdentityEvidence):
        raise SecurityViolation("Evidencia de identidad ausente")
    if (value.container != "appcontainer" or value.integrity_level != "low"
            or value.elevated is not False
            or value.privileges != ("SeChangeNotifyPrivilege",)
            or value.capabilities != () or value.credential_access is not False):
        raise SecurityViolation("La identidad del worker no cumple mínimo privilegio")


class RestrictedIdentityLauncher(Protocol):
    """Frontera del futuro lanzador nativo, inyectable para pruebas."""

    def prepare(self) -> RestrictedIdentityEvidence: ...


@dataclass(frozen=True)
class PreparedNativeIdentity:
    """Recursos nativos listos, pero todavía no autorizados para lanzamiento."""
    primary_token: int
    appcontainer_sid: int
    token_state: PreparedTokenState


class NativeIdentityBackend(Protocol):
    def prepare(self, appcontainer_name: str) -> PreparedNativeIdentity: ...
    def close(self, prepared: PreparedNativeIdentity) -> None: ...


def prepare_native_identity(
    backend: NativeIdentityBackend, appcontainer_name: str = "CubicadorIA.OcrWorker",
) -> PreparedNativeIdentity:
    """Prepara recursos Win32 y valida todo lo comprobable antes del proceso.

    ``container`` solo puede afirmarse tras consultar el token del proceso hijo.
    Por eso el snapshot previo usa ``pending-appcontainer`` y esta función exige
    las restantes propiedades, sin convertirlas en evidencia del gate.
    """
    if (not isinstance(appcontainer_name, str)
            or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,62}[A-Za-z0-9])?", appcontainer_name) is None
            or ".." in appcontainer_name or "\x00" in appcontainer_name):
        raise SecurityViolation("Nombre AppContainer inválido")
    prepared = backend.prepare(appcontainer_name)
    if not isinstance(prepared, PreparedNativeIdentity):
        raise SecurityViolation("Backend nativo devolvió un tipo inválido")
    value = prepared.token_state
    if (not prepared.primary_token or not prepared.appcontainer_sid
            or not isinstance(value, PreparedTokenState)
            or value.container_status != "pending"
            or value.capabilities_status != "unknown"
            or value.credential_access_status != "unknown"
            or value.integrity_level != "low" or value.elevated is not False
            or value.privileges != ("SeChangeNotifyPrivilege",)
            ):
        backend.close(prepared)
        raise SecurityViolation("Preparación nativa de identidad incompleta")
    return prepared


class CtypesWindowsIdentityBackend:
    """WinAPI real para token restringido y preparación del SID AppContainer.

    El perfil AppContainer debe haber sido creado por el instalador. Este código
    no crea ni elimina perfiles silenciosamente. Las capabilities siguen como
    ``unknown`` hasta inspeccionar el token del proceso hijo suspendido.
    """

    TOKEN_QUERY = 0x0008
    TOKEN_DUPLICATE = 0x0002
    TOKEN_ASSIGN_PRIMARY = 0x0001
    TOKEN_ADJUST_DEFAULT = 0x0080
    DISABLE_MAX_PRIVILEGE = 0x1
    TOKEN_PRIMARY = 1
    SECURITY_IMPERSONATION = 2

    def __init__(self) -> None:
        if os.name != "nt":
            raise SecurityViolation("WinAPI de identidad solo disponible en Windows")
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.wintypes = ctypes, wintypes
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.userenv = ctypes.WinDLL("userenv", use_last_error=True)
        self.advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                   ctypes.POINTER(wintypes.HANDLE)]
        self.advapi32.OpenProcessToken.restype = wintypes.BOOL
        self.advapi32.CreateRestrictedToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
            wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID,
            wintypes.DWORD, wintypes.LPVOID, ctypes.POINTER(wintypes.HANDLE)]
        self.advapi32.CreateRestrictedToken.restype = wintypes.BOOL
        self.advapi32.DuplicateTokenEx.argtypes = [wintypes.HANDLE, wintypes.DWORD,
            wintypes.LPVOID, ctypes.c_int, ctypes.c_int, ctypes.POINTER(wintypes.HANDLE)]
        self.advapi32.DuplicateTokenEx.restype = wintypes.BOOL
        self.advapi32.SetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                       wintypes.LPVOID, wintypes.DWORD]
        self.advapi32.SetTokenInformation.restype = wintypes.BOOL
        self.advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int,
            wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.advapi32.GetTokenInformation.restype = wintypes.BOOL
        self.advapi32.ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR,
                                                         ctypes.POINTER(wintypes.LPVOID)]
        self.advapi32.ConvertStringSidToSidW.restype = wintypes.BOOL
        self.advapi32.GetLengthSid.argtypes = [wintypes.LPVOID]
        self.advapi32.GetLengthSid.restype = wintypes.DWORD
        self.advapi32.FreeSid.argtypes = [wintypes.LPVOID]
        self.advapi32.FreeSid.restype = wintypes.LPVOID
        self.advapi32.LookupPrivilegeNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPVOID,
                                                       wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.advapi32.LookupPrivilegeNameW.restype = wintypes.BOOL
        self.advapi32.GetSidSubAuthorityCount.argtypes = [wintypes.LPVOID]
        self.advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        self.advapi32.GetSidSubAuthority.argtypes = [wintypes.LPVOID, wintypes.DWORD]
        self.advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
        self.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        self.kernel32.LocalFree.restype = wintypes.HLOCAL
        self.userenv.DeriveAppContainerSidFromAppContainerName.argtypes = [
            wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID)]
        self.userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long

    def _check(self, ok: object, operation: str) -> None:
        if not ok:
            raise OSError(self.ctypes.get_last_error(), operation)

    def _token_bytes(self, token: int, info_class: int) -> object:
        c, w = self.ctypes, self.wintypes
        needed = w.DWORD()
        self.advapi32.GetTokenInformation(token, info_class, None, 0, c.byref(needed))
        if not needed.value:
            raise OSError(c.get_last_error(), "GetTokenInformation(size)")
        buffer = c.create_string_buffer(needed.value)
        self._check(self.advapi32.GetTokenInformation(token, info_class, buffer,
                    needed.value, c.byref(needed)), "GetTokenInformation")
        return buffer

    def _snapshot(self, token: int) -> PreparedTokenState:
        c, w = self.ctypes, self.wintypes
        elevation = self._token_bytes(token, 20)
        elevated = bool(c.cast(elevation, c.POINTER(w.DWORD)).contents.value)
        integrity = self._token_bytes(token, 25)
        class SID_AND_ATTRIBUTES(c.Structure):
            _fields_ = [("Sid", w.LPVOID), ("Attributes", w.DWORD)]
        label = c.cast(integrity, c.POINTER(SID_AND_ATTRIBUTES)).contents
        adv = self.advapi32
        count = adv.GetSidSubAuthorityCount(label.Sid).contents.value
        if count < 1:
            raise SecurityViolation("SID de integridad inválido")
        rid = adv.GetSidSubAuthority(label.Sid, count - 1).contents.value
        level = "low" if rid == 0x1000 else "other"
        privilege_data = self._token_bytes(token, 3)
        class LUID(c.Structure):
            _fields_ = [("LowPart", w.DWORD), ("HighPart", c.c_long)]
        class LUID_AND_ATTRIBUTES(c.Structure):
            _fields_ = [("Luid", LUID), ("Attributes", w.DWORD)]
        count_privileges = c.cast(privilege_data, c.POINTER(w.DWORD)).contents.value
        offset = (c.sizeof(w.DWORD) + c.alignment(LUID_AND_ATTRIBUTES) - 1) & ~(c.alignment(LUID_AND_ATTRIBUTES) - 1)
        array_type = LUID_AND_ATTRIBUTES * count_privileges
        entries = c.cast(c.addressof(privilege_data) + offset, c.POINTER(array_type)).contents
        names: list[str] = []
        for entry in entries:
            size = w.DWORD(0)
            self.advapi32.LookupPrivilegeNameW(None, c.byref(entry.Luid), None, c.byref(size))
            name = c.create_unicode_buffer(size.value + 1)
            self._check(self.advapi32.LookupPrivilegeNameW(None, c.byref(entry.Luid), name,
                        c.byref(size)), "LookupPrivilegeNameW")
            # Solo privilegios habilitados cuentan como autoridad efectiva.
            if entry.Attributes & 0x2:
                names.append(name.value)
        privileges = tuple(sorted(names))
        return PreparedTokenState(level, elevated, privileges)

    def verify_child_token(self, token: int) -> ChildTokenState:
        """Verifica el token real del hijo luego de CreateProcess suspendido."""
        c, w = self.ctypes, self.wintypes
        snapshot = self._snapshot(token)
        is_container_data = self._token_bytes(token, 29)
        is_container = bool(c.cast(is_container_data, c.POINTER(w.DWORD)).contents.value)
        capabilities_data = self._token_bytes(token, 30)
        capability_count = c.cast(capabilities_data, c.POINTER(w.DWORD)).contents.value
        return ChildTokenState(is_container, snapshot.integrity_level,
                               snapshot.elevated, snapshot.privileges,
                               capability_count)

    def prepare(self, appcontainer_name: str) -> PreparedNativeIdentity:
        c, w = self.ctypes, self.wintypes
        current = w.HANDLE(); restricted = w.HANDLE(); primary = w.HANDLE()
        low_sid = w.LPVOID(); app_sid = w.LPVOID()
        access = self.TOKEN_QUERY | self.TOKEN_DUPLICATE | self.TOKEN_ASSIGN_PRIMARY | self.TOKEN_ADJUST_DEFAULT
        try:
            self._check(self.advapi32.OpenProcessToken(self.kernel32.GetCurrentProcess(), access,
                        c.byref(current)), "OpenProcessToken")
            self._check(self.advapi32.CreateRestrictedToken(current, self.DISABLE_MAX_PRIVILEGE,
                        0, None, 0, None, 0, None, c.byref(restricted)), "CreateRestrictedToken")
            self._check(self.advapi32.DuplicateTokenEx(restricted, access, None,
                        self.SECURITY_IMPERSONATION, self.TOKEN_PRIMARY, c.byref(primary)),
                        "DuplicateTokenEx")
            self._check(self.advapi32.ConvertStringSidToSidW("S-1-16-4096", c.byref(low_sid)),
                        "ConvertStringSidToSidW")
            class TOKEN_MANDATORY_LABEL(c.Structure):
                _fields_ = [("Sid", w.LPVOID), ("Attributes", w.DWORD)]
            label = TOKEN_MANDATORY_LABEL(low_sid, 0x20)
            sid_length = self.advapi32.GetLengthSid(low_sid)
            self._check(self.advapi32.SetTokenInformation(primary, 25, c.byref(label),
                        c.sizeof(label) + sid_length), "SetTokenInformation(Low)")
            hr = self.userenv.DeriveAppContainerSidFromAppContainerName(appcontainer_name,
                                                                         c.byref(app_sid))
            if hr != 0:
                raise OSError(hr, "DeriveAppContainerSidFromAppContainerName")
            evidence = self._snapshot(primary)
            return PreparedNativeIdentity(int(primary.value), int(app_sid.value), evidence)
        except Exception:
            if primary: self.kernel32.CloseHandle(w.HANDLE(primary.value))
            if app_sid: self.advapi32.FreeSid(app_sid)
            raise
        finally:
            if low_sid: self.kernel32.LocalFree(low_sid)
            if restricted: self.kernel32.CloseHandle(w.HANDLE(restricted.value))
            if current: self.kernel32.CloseHandle(w.HANDLE(current.value))

    def close(self, prepared: PreparedNativeIdentity) -> None:
        self.kernel32.CloseHandle(self.wintypes.HANDLE(prepared.primary_token))
        self.advapi32.FreeSid(self.wintypes.LPVOID(prepared.appcontainer_sid))


class WindowsAppContainerLauncher:
    """Marcador de producción: nunca afirma controles que aún no ejecuta."""

    def prepare(self) -> RestrictedIdentityEvidence:
        if os.name != "nt":
            raise SecurityViolation("La identidad AppContainer solo existe en Windows")
        raise SecurityViolation(
            "Lanzador AppContainer nativo pendiente de validación Windows 10/11"
        )


class EphemeralJobKeys:
    """Secretos en memoria con operaciones acotadas y borrado best-effort."""

    __slots__ = ("_session", "_nonce", "job_id", "_closed")

    def __init__(self, job_id: str):
        if not isinstance(job_id, str) or len(job_id) != 32:
            raise SecurityViolation("job_id inválido para llaves efímeras")
        self.job_id = job_id
        self._session = bytearray(secrets.token_bytes(32))
        self._nonce = bytearray(secrets.token_bytes(32))
        self._closed = False

    def sign(self, message: bytes) -> bytes:
        self._ensure_open()
        if not isinstance(message, bytes) or len(message) > 1024 * 1024:
            raise SecurityViolation("Mensaje inválido para autenticar")
        mac = hmac.new(self._session, digestmod="sha256")
        mac.update(message)
        mac.update(self._nonce)
        return mac.digest()

    def verify(self, message: bytes, signature: bytes) -> bool:
        if not isinstance(signature, bytes) or len(signature) != 32:
            return False
        return hmac.compare_digest(self.sign(message), signature)

    def audit_fingerprint(self) -> str:
        """Huella no reversible; no expone el material secreto."""
        self._ensure_open()
        return hashlib.sha256(memoryview(self._session)).hexdigest()[:16]

    def _ensure_open(self) -> None:
        if self._closed:
            raise SecurityViolation("Llaves efímeras ya destruidas")

    def close(self) -> None:
        if not self._closed:
            self._session[:] = b"\x00" * len(self._session)
            self._nonce[:] = b"\x00" * len(self._nonce)
            self._closed = True

    def __enter__(self) -> "EphemeralJobKeys":
        self._ensure_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def prepare_worker_identity(
    launcher: RestrictedIdentityLauncher | None = None,
) -> RestrictedIdentityEvidence:
    selected = launcher or WindowsAppContainerLauncher()
    evidence = selected.prepare()
    validate_restricted_identity(evidence)
    return evidence
