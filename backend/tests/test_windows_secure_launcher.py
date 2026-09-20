from __future__ import annotations

import unittest
import ctypes
from ctypes import wintypes
from pathlib import Path

from cubicador.security import SecurityViolation
from cubicador.windows_identity import ChildTokenState, PreparedNativeIdentity, PreparedTokenState
from cubicador.windows_secure_launcher import (
    CtypesSecureLaunchBackend, SuspendedChild, create_verified_suspended_child,
)


class FakeBackend:
    def __init__(self, state=None, fail_at=None):
        self.state = state or ChildTokenState(True, "low", False,
            ("SeChangeNotifyPrivilege",), 0, True, True)
        self.fail_at = fail_at
        self.events = []
        self.prepared = PreparedNativeIdentity(10, 20,
            PreparedTokenState("low", False, ("SeChangeNotifyPrivilege",)))
        self.child = SuspendedChild(30, 40, 50, 60, "a" * 64)

    def prepare(self, _name): self.events.append("prepare"); return self.prepared
    def close(self, _prepared): self.events.append("close-prepared")
    close_prepared = close
    def create_suspended(self, _prepared, _argv, _cwd, _handles, _policy, verifier):
        self.events.append("attest")
        verifier()
        self.events.append("create-suspended")
        if self.fail_at == "create": raise OSError("create")
        return self.child
    def inspect_child(self, _child, _prepared): self.events.append("inspect"); return self.state
    def assign_to_job(self, _child):
        self.events.append("assign")
        if self.fail_at == "assign": raise OSError("assign")
    def resume(self, _child): self.events.append("resume")
    def abort(self, child):
        self.events.append("abort" if child else "abort-none")
        if self.fail_at == "abort": raise OSError("abort")


class FakeAudit:
    def __init__(self, backend, fail=False): self.backend, self.fail, self.last, self.history = backend, fail, None, []
    def append(self, event, status, **metrics):
        self.last = (event, status, metrics)
        self.history.append(self.last)
        self.backend.events.append("audit")
        if self.fail: raise OSError("audit")


def launch(backend, audit=None):
    return create_verified_suspended_child(
        backend, ("C:/vendor/worker.exe",), Path("C:/work"), (1, 2, 3),
        audit=audit or FakeAudit(backend), launch_verifier=lambda: Path("C:/vendor/worker.exe"))


class SecureLauncherTests(unittest.TestCase):
    def test_order_is_create_assign_inspect_audit_resume(self):
        backend = FakeBackend()
        audit = FakeAudit(backend)
        prepared, child = launch(backend, audit)
        self.assertIs(prepared, backend.prepared)
        self.assertIs(child, backend.child)
        self.assertEqual(backend.events, ["prepare", "attest", "create-suspended", "assign", "inspect", "audit", "resume"])
        event, status, metrics = audit.last
        self.assertEqual((event, status), ("gate-decision", "approved"))
        self.assertEqual(metrics["worker_pid"], 50)
        self.assertEqual(metrics["executable_sha256"], "a" * 64)
        self.assertEqual(metrics["decision"], "approved")

    def test_invalid_child_is_never_assigned_or_resumed_and_is_aborted(self):
        states = (
            ChildTokenState(False, "low", False, ("SeChangeNotifyPrivilege",), 0, True, True),
            ChildTokenState(True, "medium", False, ("SeChangeNotifyPrivilege",), 0, True, True),
            ChildTokenState(True, "low", True, ("SeChangeNotifyPrivilege",), 0, True, True),
            ChildTokenState(True, "low", False, ("SeDebugPrivilege",), 0, True, True),
            ChildTokenState(True, "low", False, ("SeChangeNotifyPrivilege",), 1, True, True),
            ChildTokenState(True, "low", False, ("SeChangeNotifyPrivilege",), 0, False, True),
            ChildTokenState(True, "low", False, ("SeChangeNotifyPrivilege",), 0, True, False),
        )
        for state in states:
            backend = FakeBackend(state)
            with self.subTest(state=state), self.assertRaises(SecurityViolation):
                launch(backend)
            self.assertNotIn("resume", backend.events)
            self.assertEqual(backend.events[-2:], ["abort", "close-prepared"])

    def test_assign_failure_aborts_suspended_child(self):
        backend = FakeBackend(fail_at="assign")
        with self.assertRaises(OSError):
            launch(backend)
        self.assertNotIn("resume", backend.events)
        self.assertEqual(backend.events[-2:], ["abort", "close-prepared"])

    def test_create_failure_closes_prepared_without_resume(self):
        backend = FakeBackend(fail_at="create")
        with self.assertRaises(OSError):
            launch(backend)
        self.assertEqual(backend.events[-2:], ["create-suspended", "close-prepared"])

    def test_audit_is_mandatory_and_failure_aborts_before_resume(self):
        backend = FakeBackend()
        with self.assertRaises(SecurityViolation):
            create_verified_suspended_child(backend, ("C:/w.exe",), Path("C:/work"), (1, 2, 3),
                                              launch_verifier=lambda: Path("C:/w.exe"))
        self.assertEqual(backend.events, [])
        backend = FakeBackend()
        with self.assertRaises(OSError): launch(backend, FakeAudit(backend, fail=True))
        self.assertNotIn("resume", backend.events)
        self.assertEqual(backend.events[-2:], ["abort", "close-prepared"])

    def test_rejection_is_audited_without_exception_detail(self):
        backend = FakeBackend(ChildTokenState(False, "low", False,
            ("SeChangeNotifyPrivilege",), 0, True, True))
        audit = FakeAudit(backend)
        with self.assertRaises(SecurityViolation): launch(backend, audit)
        event, status, metrics = audit.history[-1]
        self.assertEqual((event, status, metrics["decision"]),
                         ("gate-decision", "rejected", "rejected"))
        self.assertEqual(metrics["detail_code"], "pre_resume_failure")
        self.assertNotIn("exception", metrics)

    def test_abort_failure_still_closes_prepared_exactly_once(self):
        backend = FakeBackend(fail_at="abort")
        backend.state = ChildTokenState(False, "low", False,
            ("SeChangeNotifyPrivilege",), 0, True, True)
        with self.assertRaisesRegex(SecurityViolation, "cleanup"):
            launch(backend)
        self.assertEqual(backend.events.count("abort"), 1)
        self.assertEqual(backend.events.count("close-prepared"), 1)


class _FakeKernel:
    def __init__(self, fail):
        self.fail, self.query_calls, self.closed = fail, 0, []
    def WaitForSingleObject(self, *_): return 0
    def GetExitCodeProcess(self, _handle, pointer):
        if self.fail == "exit": return False
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = 0
        return True
    def QueryInformationJobObject(self, _job, _cls, pointer, _size, _ret):
        self.query_calls += 1
        if self.fail == "query" and self.query_calls == 1: return False
        # ActiveProcesses está tras cuatro LARGE_INTEGER y dos DWORD.
        address = ctypes.cast(pointer, ctypes.c_void_p).value
        ctypes.cast(address + 40, ctypes.POINTER(wintypes.DWORD)).contents.value = 0
        return True
    def TerminateJobObject(self, *_): return True
    def TerminateProcess(self, *_): return True
    def CloseHandle(self, handle): self.closed.append(int(handle.value or 0)); return True


class NativeOwnershipTests(unittest.TestCase):
    @staticmethod
    def backend(fail=None):
        backend = object.__new__(CtypesSecureLaunchBackend)
        backend.ctypes, backend.wintypes = ctypes, wintypes
        backend.kernel32 = _FakeKernel(fail)
        backend._owned = {50: (30, 40, 60)}
        backend._assigned = {50}
        backend._check = lambda ok, operation: None if ok else (_ for _ in ()).throw(OSError(operation))
        return backend

    def test_get_exit_and_query_failures_still_cleanup_once(self):
        child = SuspendedChild(30, 40, 50, 60, "a" * 64)
        for failure in ("exit", "query"):
            backend = self.backend(failure)
            with self.subTest(failure=failure), self.assertRaises(OSError):
                backend.wait_and_close(child, 100)
            self.assertNotIn(50, backend._owned)
            self.assertEqual(sorted(backend.kernel32.closed), [30, 40, 60])

    def test_normal_close_rejects_second_close(self):
        backend = self.backend()
        child = SuspendedChild(30, 40, 50, 60, "a" * 64)
        self.assertEqual(backend.wait_and_close(child, 100), 0)
        with self.assertRaisesRegex(SecurityViolation, "ya cerrado"):
            backend.wait_and_close(child, 100)
        backend.abort(child)  # idempotente; no vuelve a cerrar handles.
        self.assertEqual(sorted(backend.kernel32.closed), [30, 40, 60])


if __name__ == "__main__": unittest.main()
