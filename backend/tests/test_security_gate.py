import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from cubicador.security import SecurityViolation
from cubicador.security_gate import require_security_gate, validate_security_gate


class SecurityGateTests(unittest.TestCase):
    def setUp(self):
        self.real = Path(__file__).parents[1] / "security-gate.json"

    def test_real_gate_is_valid_but_closed(self):
        self.assertEqual(validate_security_gate(self.real), [])
        with self.assertRaises(SecurityViolation):
            require_security_gate(self.real)

    def test_gate_cannot_approve_with_missing_evidence(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "gate.json"
            spec = json.loads(self.real.read_text("utf-8")); spec["approved"] = True
            path.write_text(json.dumps(spec), "utf-8")
            self.assertIn("gate aprobado sin todas las evidencias", validate_security_gate(path))

    def test_only_exact_external_pin_and_complete_evidence_authorize(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "gate.json"
            spec = json.loads(self.real.read_text("utf-8")); spec["approved"] = True
            spec["evidence"] = {key: True for key in spec["evidence"]}
            path.write_text(json.dumps(spec, sort_keys=True), "utf-8")
            pin = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertTrue(require_security_gate(path, pin)["approved"])
            with self.assertRaises(SecurityViolation): require_security_gate(path, "1" * 64)

    def test_privilege_key_resource_and_audit_regressions_are_rejected(self):
        mutations = [
            ("worker_identity", "run_as_administrator", True),
            ("worker_identity", "network_capabilities", ["internetClient"]),
            ("keys", "persistent_secrets", True),
            ("audit", "windows_event_ids", [5156]),
            ("audit", "max_bytes", -1),
            ("resources", "allowed_read", ["signed-vendor-bundle", "job-input", "user-profile"]),
            ("audit", "required_events", ["outbound-connection-allowed", "outbound-connection-blocked"]),
            ("audit", "required_fields", ["job_id", "worker_pid", "destination_ip", "destination_port", "decision"]),
        ]
        for section, field, value in mutations:
            with self.subTest(section=section, field=field), tempfile.TemporaryDirectory() as raw:
                path = Path(raw) / "gate.json"; spec = json.loads(self.real.read_text("utf-8"))
                spec[section][field] = value; path.write_text(json.dumps(spec), "utf-8")
                self.assertTrue(validate_security_gate(path))
