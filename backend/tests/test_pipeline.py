import subprocess
import json
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from openpyxl import load_workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from cubicador.locator import locate_summary_table
from cubicador.pdf_text import PdfText
from cubicador.pipeline import process_pdf
from cubicador.adapters import parse_number
from cubicador.adapters import LlamaServerInterpreter
from cubicador.runtime import AuditStore, JobWorkspace, run_command, safe_output_path
from cubicador.security import SecurityPolicy, SecurityViolation


CONTROLLED_PDFTOTEXT = (
    (Path(__file__).resolve().parents[2] / "vendor" / "poppler" / "bin" / "pdftotext.exe").is_file()
    or Path("/usr/bin/pdftotext").is_file()
    or Path("/usr/local/bin/pdftotext").is_file()
)


def make_pdf(path: Path) -> None:
    document = SimpleDocTemplate(str(path), pagesize=A4)
    data = [["Ítem", "Descripción", "Unidad", "Cantidad"], ["1.1", "Hormigón H30", "m3", "125,50"], ["1.2", "Acero A630-420H", "kg", "8.450,00"], ["1.3", "Moldaje de fundación", "m2", "310,25"]]
    table = Table(data, colWidths=[60, 220, 70, 90])
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
    document.build([Paragraph("PLANO DE FUNDACIONES", None), Spacer(1, 250), Paragraph("TABLA RESUMEN DE CANTIDADES", None), table, Paragraph("Notas: Cantidades referenciales", None)])


class LocatorTests(unittest.TestCase):
    def test_accepts_cubicacion_title_aliases(self):
        for title in ("TABLA DE CUBICACIÓN", "CUADRO DE CUBICACION"):
            with self.subTest(title=title):
                text = PdfText((f"{title}\nÍtem  Descripción  Unidad  Cantidad\n1  Hormigón  m3  12,5",))
                candidate = locate_summary_table(text)
                self.assertIsNotNone(candidate)
                self.assertEqual(candidate.title, title)

    def test_prefers_summary_table(self):
        text = PdfText(("OTRA TABLA\nItem  Cantidad\n1  2\nRESUMEN DE CANTIDADES\nÍtem  Descripción  Unidad  Cantidad\n1  Hormigón  m3  12,5",))
        candidate = locate_summary_table(text)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.title, "RESUMEN DE CANTIDADES")

    def test_does_not_truncate_long_text(self):
        raw = "x" * 70000 + "\nRESUMEN DE CANTIDADES\nÍtem  Descripción  Unidad  Cantidad\n1  Acero  kg  2"
        document = PdfText((raw,), raw=raw)
        self.assertGreater(len(document.full_text), 60000)
        self.assertIsNotNone(locate_summary_table(document))

    def test_locale_numbers(self):
        for raw, expected in [("8.450,00", 8450), ("8,450.00", 8450), ("125.50", 125.5), ("125,50", 125.5), ("125", 125)]:
            self.assertEqual(parse_number(raw)[:2], (expected, "parsed"))
        self.assertEqual(parse_number("1.234")[1], "ambiguous")


class PipelineTests(unittest.TestCase):
    @unittest.skipUnless(CONTROLLED_PDFTOTEXT, "Poppler controlado no está empaquetado en este runner")
    def test_pdf_to_json_and_excel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf, xlsx = root / "plano.pdf", root / "cantidades.xlsx"
            make_pdf(pdf)
            result = process_pdf(pdf, xlsx, output_root=root)
            self.assertTrue(result.tabla_encontrada)
            self.assertEqual(len(result.filas), 3)
            self.assertEqual(result.filas[1].quantities[0].numeric_value, 8450.0)
            workbook = load_workbook(xlsx)
            self.assertEqual(workbook.active.max_row, 4)
            self.assertEqual(workbook.sheetnames, ["Cantidades", "Pendientes", "Trazabilidad"])
            self.assertIn("Acero A630-420H", result.filas[1].evidencia.text)
            self.assertEqual(result.filas[1].quantities[0].original, "8.450,00")
            audit_files = list((root / ".cubicador-audit").glob("*.jsonl"))
            self.assertEqual(len(audit_files), 1)
            audit_text = audit_files[0].read_text("ascii")
            self.assertIn('"job_completed"', audit_text)
            self.assertNotIn("Hormigón", audit_text)

    def test_preserves_empty_page_numbering(self):
        from cubicador.adapters import HeuristicInterpreter
        candidate = locate_summary_table(PdfText(("", "RESUMEN DE CANTIDADES\nÍtem  Descripción  Unidad  Cantidad\n1  Hormigón  m3  2,5")))
        result = HeuristicInterpreter().interpret(candidate, "plano.pdf")
        self.assertEqual(result.filas[0].pagina, 2)

    def test_multiple_quantity_columns_are_not_lost(self):
        from cubicador.adapters import HeuristicInterpreter
        candidate = locate_summary_table(PdfText(("RESUMEN DE CANTIDADES\nItem  Descripcion  m3  kg\n1  Fundacion  12,5  450,0",)))
        result = HeuristicInterpreter().interpret(candidate, "plano.pdf")
        self.assertEqual([q.column for q in result.filas[0].quantities], ["m3", "kg"])
        self.assertEqual([q.numeric_value for q in result.filas[0].quantities], [12.5, 450.0])

    @unittest.skipUnless(CONTROLLED_PDFTOTEXT, "Poppler controlado no está empaquetado en este runner")
    def test_absent_table(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "none.pdf"
            SimpleDocTemplate(str(path)).build([Paragraph("Plano sin tabla", None)])
            result = process_pdf(path)
            self.assertFalse(result.tabla_encontrada)

    @unittest.skipUnless(CONTROLLED_PDFTOTEXT, "Poppler controlado no está empaquetado en este runner")
    def test_warns_when_visible_text_may_be_cad_geometry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cad.pdf"
            SimpleDocTemplate(str(path)).build([Paragraph("ESCALA FECHA REV.", None)])
            result = process_pdf(path)
            self.assertFalse(result.tabla_encontrada)
            self.assertTrue(result.requiere_revision)
            self.assertTrue(any("geometría CAD" in warning for warning in result.advertencias))

    @unittest.skipUnless(CONTROLLED_PDFTOTEXT, "Poppler controlado no está empaquetado en este runner")
    def test_cli_writes_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf, xlsx, json_path = root / "plano.pdf", root / "out.xlsx", root / "out.json"
            make_pdf(pdf)
            completed = subprocess.run([sys.executable, "-m", "cubicador.cli", str(pdf), "--output-dir", str(root), "--excel", str(xlsx), "--json", str(json_path)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(xlsx.exists() and json_path.exists())


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self._runtime_temp = tempfile.TemporaryDirectory()
        self.runtime_workspace = Path(self._runtime_temp.name)

    def tearDown(self):
        self._runtime_temp.cleanup()

    def test_policy_rejects_invalid_or_incoherent_limits(self):
        for kwargs in (
            {"max_pdf_bytes": 0}, {"max_queued_jobs": -1},
            {"max_output_bytes": 20, "max_temp_bytes": 10},
            {"allowed_model_ports": (0,)}, {"allowed_model_ports": ()},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SecurityPolicy(**kwargs)

    def test_policy_uses_bounded_plan_defaults(self):
        policy = SecurityPolicy()
        self.assertEqual(policy.max_pdf_bytes, 50 * 1024 * 1024)
        self.assertEqual(policy.max_process_output_bytes, 2 * 1024 * 1024)
        self.assertEqual(policy.max_queued_jobs, 2)

    def test_rejects_non_loopback_model_endpoints(self):
        invalid = (
            "https://127.0.0.1:8080/v1/chat/completions",
            "http://example.com:8080/v1/chat/completions",
            "http://127.0.0.1:8080@evil.test/v1/chat/completions",
            "http://127.0.0.1:9999/v1/chat/completions",
            "http://127.0.0.1:8080/other",
        )
        for endpoint in invalid:
            with self.subTest(endpoint=endpoint), self.assertRaises(SecurityViolation):
                LlamaServerInterpreter(endpoint)

    def test_model_refuses_redirect_and_second_request(self):
        interpreter = LlamaServerInterpreter()
        candidate = locate_summary_table(PdfText(("TABLA DE CUBICACIÓN\nItem  Descripción  Unidad  Cantidad\n1  H  m3  2",)))
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 302
        with patch("urllib.request.OpenerDirector.open", side_effect=SecurityViolation("redirect")):
            with self.assertRaises(SecurityViolation):
                interpreter.interpret(candidate, "x.pdf")
        with self.assertRaises(SecurityViolation):
            interpreter.interpret(candidate, "x.pdf")

    def test_input_size_and_output_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "large.pdf"
            pdf.write_bytes(b"%PDF" + b"x" * 20)
            with self.assertRaises(SecurityViolation):
                SecurityPolicy(max_pdf_bytes=10).validate_pdf(pdf)
            with self.assertRaises(SecurityViolation):
                safe_output_path(root.parent / "escape.xlsx", root, ".xlsx")
            target = root / "real.pdf"; target.write_bytes(b"%PDF-x")
            link = root / "linked.pdf"; link.symlink_to(target)
            with self.assertRaises(SecurityViolation):
                SecurityPolicy().validate_pdf(link)

    def test_rejects_fake_pdf_unc_ads_and_unconfined_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake = root / "fake.pdf"; fake.write_bytes(b"not-a-pdf")
            with self.assertRaises(SecurityViolation):
                SecurityPolicy().validate_pdf(fake)
            for value in (r"\\server\share\x.pdf", "file.pdf:stream"):
                with self.assertRaises(SecurityViolation):
                    SecurityPolicy().validate_pdf(value)
            valid = root / "valid.pdf"; make_pdf(valid)
            with self.assertRaises(SecurityViolation):
                process_pdf(valid, root / "out.xlsx")

    def test_subprocess_output_is_bounded_during_execution(self):
        policy = SecurityPolicy(max_process_output_bytes=1024, windows_job_objects_enabled=sys.platform == "win32")
        with self.assertRaises(SecurityViolation):
            run_command([sys.executable, "-c", "import os; os.write(1, b'x' * 1000000)"], policy=policy, workspace=self.runtime_workspace)

    def test_fast_exit_output_is_checked_before_reading(self):
        policy = SecurityPolicy(max_process_output_bytes=64, windows_job_objects_enabled=sys.platform == "win32")
        with self.assertRaises(SecurityViolation):
            run_command([sys.executable, "-c", "import os; os.write(1, b'x' * 4096)"], policy=policy, workspace=self.runtime_workspace)

    def test_workspace_quota_is_enforced(self):
        workspace = JobWorkspace(SecurityPolicy(max_temp_bytes=16, max_output_bytes=8, max_response_bytes=8))
        with workspace as path:
            (path / "overflow.bin").write_bytes(b"x" * 17)
            with self.assertRaises(SecurityViolation):
                workspace.check_quota()

    def test_audit_is_bounded_and_rejects_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = SecurityPolicy(max_audit_bytes=2000)
            audit = AuditStore(directory, policy).start_job()
            audit.append("job_started", "ok", bytes=12)
            content = audit.path.read_text("ascii")
            self.assertIn('"job_started"', content)
            self.assertNotIn("plano", content)
            with self.assertRaises(SecurityViolation):
                audit.append("event", "ok", document_text="secreto")
            with self.assertRaises(SecurityViolation):
                for _ in range(20):
                    audit.append("tick", "ok")

    def test_audit_hash_chain_terminal_and_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            policy = SecurityPolicy(max_audit_files=2)
            store = AuditStore(directory, policy)
            first = store.start_job()
            first.append("job_started", "ok")
            first.append("job_completed", "ok")
            records = [json.loads(line) for line in first.path.read_text("ascii").splitlines()]
            self.assertEqual(records[1]["previous_hash"], records[0]["event_hash"])
            self.assertEqual([row["seq"] for row in records], [1, 2])
            self.assertEqual(records[0]["schema"], 1)
            self.assertEqual(len(records[0]["policy_hash"]), 64)
            with self.assertRaises(SecurityViolation):
                first.append("tick", "ok")
            second = store.start_job(); second.append("job_completed", "ok")
            store.start_job()
            self.assertLessEqual(len(list((Path(directory) / ".cubicador-audit").glob("*.jsonl"))), 2)

    def test_audit_verifier_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AuditStore(directory)
            audit = store.start_job(); audit.append("job_started", "ok"); audit.append("job_completed", "ok")
            self.assertTrue(store.verify(audit.path))
            data = audit.path.read_bytes().replace(b'"status":"ok"', b'"status":"xx"', 1)
            audit.path.write_bytes(data)
            self.assertFalse(store.verify(audit.path))

    def test_windows_dispatch_is_fail_closed(self):
        with patch("cubicador.runtime._is_windows", return_value=True), patch("cubicador.runtime._run_windows_job", side_effect=OSError("job failed")) as runner:
            with self.assertRaises(OSError):
                run_command([sys.executable, "-c", "print(1)"], workspace=self.runtime_workspace, policy=SecurityPolicy(windows_job_objects_enabled=True))
            runner.assert_called_once()

    def test_workspace_is_cleaned(self):
        workspace = JobWorkspace()
        with workspace as path:
            (path / "temporary.txt").write_text("dato")
            self.assertTrue(path.exists())
        self.assertFalse(path.exists())

    def test_subprocess_timeout_and_cancel(self):
        import threading
        cancelled = threading.Event(); cancelled.set()
        with self.assertRaises(Exception):
            run_command([sys.executable, "-c", "print('no')"], cancel=cancelled, workspace=self.runtime_workspace)
        with self.assertRaises(TimeoutError):
            run_command([sys.executable, "-c", "import time; time.sleep(2)"], timeout=1, workspace=self.runtime_workspace,
                policy=SecurityPolicy(windows_job_objects_enabled=sys.platform == "win32"))

    def test_running_subprocess_can_be_cancelled(self):
        import threading, time
        cancelled = threading.Event()
        timer = threading.Timer(0.1, cancelled.set); timer.start()
        started = time.monotonic()
        with self.assertRaises(Exception):
            run_command([sys.executable, "-c", "import time; time.sleep(5)"], cancel=cancelled, workspace=self.runtime_workspace,
                policy=SecurityPolicy(windows_job_objects_enabled=sys.platform == "win32"))
        timer.cancel()
        self.assertLess(time.monotonic() - started, 2)


if __name__ == "__main__":
    unittest.main()
