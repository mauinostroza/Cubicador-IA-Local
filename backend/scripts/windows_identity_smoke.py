"""Smoke manual. No habilita evidencia ni modifica security-gate.json."""
from __future__ import annotations

import os
import sys

from cubicador.security import SecurityViolation
from cubicador.windows_identity import (
    CtypesWindowsIdentityBackend, EphemeralJobKeys, WindowsAppContainerLauncher,
    prepare_native_identity,
)


def main() -> int:
    if sys.platform != "win32":
        print("SKIP: smoke exclusivo de Windows")
        return 0
    if os.environ.get("CUBICADOR_IDENTITY_SMOKE") != "1":
        print("SKIP: requiere CUBICADOR_IDENTITY_SMOKE=1")
        return 0
    with EphemeralJobKeys("0" * 32) as keys:
        signature = keys.sign(b"smoke")
        if not keys.verify(b"smoke", signature):
            raise AssertionError("operación efímera inválida")
    backend = CtypesWindowsIdentityBackend()
    prepared = prepare_native_identity(backend)
    try:
        if prepared.token_state.integrity_level != "low":
            raise AssertionError("token no quedó en Low Integrity")
        if prepared.token_state.privileges != ("SeChangeNotifyPrivilege",):
            raise AssertionError("token conserva privilegios adicionales")
    finally:
        backend.close(prepared)
    try:
        WindowsAppContainerLauncher().prepare()
    except SecurityViolation:
        print("PASS: token restringido/Low preparado; AppContainer no acreditado y lanzamiento cerrado")
        return 0
    raise AssertionError("El lanzador no validado no debe habilitar el worker")


if __name__ == "__main__":
    raise SystemExit(main())
