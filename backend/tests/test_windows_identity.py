from __future__ import annotations

import unittest
import sys
import tempfile
from pathlib import Path

from cubicador.security import SecurityViolation
from cubicador.runtime import run_command
from cubicador.windows_identity import (
    EphemeralJobKeys, PreparedNativeIdentity, PreparedTokenState, RestrictedIdentityEvidence,
    WindowsAppContainerLauncher, prepare_native_identity, prepare_worker_identity,
    validate_restricted_identity,
)


GOOD = RestrictedIdentityEvidence(
    container="appcontainer", integrity_level="low", elevated=False,
    privileges=("SeChangeNotifyPrivilege",), capabilities=(), credential_access=False,
)


class _Launcher:
    def __init__(self, evidence=GOOD): self.evidence = evidence
    def prepare(self): return self.evidence


class _NativeBackend:
    def __init__(self, evidence=None):
        self.evidence = evidence or PreparedTokenState(
            "low", False, ("SeChangeNotifyPrivilege",),
        )
        self.closed = False
    def prepare(self, _name): return PreparedNativeIdentity(101, 202, self.evidence)
    def close(self, _prepared): self.closed = True


class WindowsIdentityTests(unittest.TestCase):
    def test_exact_restricted_contract_is_accepted(self):
        self.assertEqual(prepare_worker_identity(_Launcher()), GOOD)

    def test_any_extra_authority_is_rejected(self):
        cases = (
            RestrictedIdentityEvidence("none", "low", False, GOOD.privileges, (), False),
            RestrictedIdentityEvidence("appcontainer", "medium", False, GOOD.privileges, (), False),
            RestrictedIdentityEvidence("appcontainer", "low", True, GOOD.privileges, (), False),
            RestrictedIdentityEvidence("appcontainer", "low", False, ("SeDebugPrivilege",), (), False),
            RestrictedIdentityEvidence("appcontainer", "low", False, GOOD.privileges, ("internetClient",), False),
            RestrictedIdentityEvidence("appcontainer", "low", False, GOOD.privileges, (), True),
        )
        for evidence in cases:
            with self.subTest(evidence=evidence), self.assertRaises(SecurityViolation):
                validate_restricted_identity(evidence)

    def test_production_launcher_fails_closed_until_native_validation(self):
        with self.assertRaises(SecurityViolation):
            WindowsAppContainerLauncher().prepare()

    def test_native_backend_is_injectable_and_validated(self):
        backend = _NativeBackend()
        prepared = prepare_native_identity(backend)
        self.assertEqual((prepared.primary_token, prepared.appcontainer_sid), (101, 202))
        self.assertFalse(backend.closed)

    def test_invalid_native_preparation_is_closed_before_rejection(self):
        bad = PreparedTokenState("medium", False, ("SeChangeNotifyPrivilege",))
        backend = _NativeBackend(bad)
        with self.assertRaises(SecurityViolation):
            prepare_native_identity(backend)
        self.assertTrue(backend.closed)

    def test_keys_are_internal_unique_and_closed(self):
        first = EphemeralJobKeys("a" * 32)
        second = EphemeralJobKeys("b" * 32)
        message = b"request"
        signature = first.sign(message)
        self.assertEqual(len(signature), 32)
        self.assertTrue(first.verify(message, signature))
        self.assertFalse(second.verify(message, signature))
        self.assertNotEqual(first.audit_fingerprint(), second.audit_fingerprint())
        self.assertFalse(hasattr(first, "session_token"))
        self.assertFalse(hasattr(first, "job_nonce"))
        first.close()
        with self.assertRaises(SecurityViolation):
            first.sign(message)
        second.close()

    def test_native_name_is_ascii_allowlisted(self):
        for name in ("bad name", "bad/child", "bad\x00name", ".bad", "bad.", "bad..name", "ócr"):
            with self.subTest(name=name), self.assertRaises(SecurityViolation):
                prepare_native_identity(_NativeBackend(), name)

    def test_preflight_evidence_can_never_bypass_secure_launcher(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
                SecurityViolation, "Lanzamiento AppContainer completo"):
            run_command([str(Path(sys.executable).resolve()), "-c", "raise SystemExit(0)"],
                        workspace=directory, require_restricted_identity=True)


if __name__ == "__main__":
    unittest.main()
