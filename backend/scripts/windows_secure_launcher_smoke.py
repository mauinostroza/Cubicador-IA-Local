"""Smoke manual destructivo solo del proceso temporal; no abre el security gate."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from cubicador.windows_secure_launcher import CtypesSecureLaunchBackend, create_verified_suspended_child
from cubicador.runtime import AuditStore


def main() -> int:
    if sys.platform != "win32":
        print("SKIP: exclusivo Windows")
        return 0
    if (os.environ.get("CUBICADOR_SECURE_LAUNCH_SMOKE") != "1"
            or os.environ.get("CUBICADOR_SECURE_FIXTURE_READY") != "1"):
        print("SKIP: requiere CUBICADOR_SECURE_LAUNCH_SMOKE=1 y CUBICADOR_SECURE_FIXTURE_READY=1 tras preparar ACL/payload")
        return 0
    import msvcrt
    print("REQUISITO: el ejecutable/payload debe tener ACL de lectura-ejecución para el AppContainer y el workspace ACL privada de escritura")
    backend = CtypesSecureLaunchBackend()
    with tempfile.TemporaryDirectory() as directory:
        handles = []
        streams = [open(Path(directory) / name, mode) for name, mode in
                   (("stdin.bin", "w+b"), ("stdout.bin", "w+b"), ("stderr.bin", "w+b"))]
        try:
            handles = [msvcrt.get_osfhandle(stream.fileno()) for stream in streams]
            audit = AuditStore(directory).start_job()
            executable = Path(sys.executable).resolve()
            prepared = None
            try:
                prepared, child = create_verified_suspended_child(backend,
                    (str(executable), "-c", "raise SystemExit(0)"),
                    Path(directory).resolve(), tuple(handles), audit=audit,
                    launch_verifier=lambda: executable)
                code = backend.wait_and_close(child, 10_000)
                if code != 0:
                    raise AssertionError(f"worker retornó {code}")
                audit.append("job_completed", "ok")
            finally:
                if prepared is not None:
                    backend.close_prepared(prepared)
        finally:
            for stream in streams: stream.close()
    print("PASS: create suspendido, token verificado, Job antes de resume; gate permanece cerrado")
    return 0


if __name__ == "__main__": raise SystemExit(main())
