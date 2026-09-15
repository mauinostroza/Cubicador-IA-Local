"""Smoke manual: ejecutar en Windows; el hijo no debe sobrevivir al timeout."""
from __future__ import annotations

import sys
import tempfile
import os
from pathlib import Path

from cubicador.runtime import run_command
from cubicador.security import SecurityPolicy


def main() -> int:
    if sys.platform != "win32":
        print("SKIP: smoke exclusivo de Windows")
        return 0
    with tempfile.TemporaryDirectory() as directory:
        policy = SecurityPolicy(windows_job_objects_enabled=True, max_process_output_bytes=4096)
        try:
            run_command([sys.executable, "-c", "print('blocked')"], workspace=directory)
        except Exception:
            pass
        else:
            raise AssertionError("El feature gate debía bloquear la política predeterminada")
        marker = Path(directory) / "child-survived.txt"
        child = f"import time,pathlib;time.sleep(4);pathlib.Path({str(marker)!r}).write_text('bad')"
        parent = f"import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',{child!r}]);time.sleep(30)"
        try:
            run_command([sys.executable, "-c", parent], timeout=1, workspace=directory, policy=policy)
        except TimeoutError:
            pass
        else:
            raise AssertionError("Se esperaba timeout")
        import time
        time.sleep(5)
        if marker.exists():
            raise AssertionError("El descendiente escapó del Job Object")
        try:
            run_command([sys.executable, "-c", "import os;os.write(1,b'x'*100000)"], workspace=directory, policy=policy)
        except Exception:
            pass
        else:
            raise AssertionError("La inundación de stdout no fue bloqueada")
        sentinel = Path(directory) / "sentinel.txt"
        with sentinel.open("wb") as inherited:
            import msvcrt
            os_handle = msvcrt.get_osfhandle(inherited.fileno())
            try:
                os.set_handle_inheritable(os_handle, True)
                leaked = Path(directory) / "handle-leaked.txt"
                probe = ("import ctypes,pathlib;flags=ctypes.c_ulong();ok=ctypes.windll.kernel32.GetHandleInformation("
                    f"{os_handle},ctypes.byref(flags));pathlib.Path({str(leaked)!r}).write_text(str(ok))")
                run_command([sys.executable, "-c", probe], workspace=directory, policy=policy)
                if leaked.read_text() != "0":
                    raise AssertionError("Se heredó un handle fuera de HANDLE_LIST")
            finally:
                os.set_handle_inheritable(os_handle, False)
    print("PASS: gate, árbol, flood y HANDLE_LIST")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
