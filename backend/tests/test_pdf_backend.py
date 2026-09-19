import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cubicador.pdf_backend import (
    PdfiumBackend, PdfiumUnavailable, PdfBackendError, _check_render_budget,
    _text_lines,
)
from cubicador.pdf_text import PdfTextError, extract_layout_text_pdfium
from cubicador.pdf_text import PdfText
from cubicador.locator import locate_summary_tables
from cubicador.security import DEFAULT_SECURITY_POLICY, SecurityPolicy, SecurityViolation


class FakeTextPage:
    def __init__(self):
        self.values = ("A", "B", "\r", "\n", "C")
        self.boxes = ((10, 100, 15, 108), (28, 100, 33, 108), (33, 100, 33, 100),
                      (33, 100, 33, 100), (10, 80, 15, 88))

    def count_chars(self):
        return len(self.values)

    def get_text_range(self, index, count):
        return self.values[index]

    def get_charbox(self, index):
        return self.boxes[index]


class PdfBackendTests(unittest.TestCase):
    def test_gate_is_closed_by_default_and_requires_worker(self):
        with self.assertRaises(PdfiumUnavailable):
            PdfiumBackend(policy=DEFAULT_SECURITY_POLICY)
        with self.assertRaises(PdfiumUnavailable):
            PdfiumBackend(policy=SecurityPolicy(pdfium_enabled=True))
        with self.assertRaises(PdfTextError):
            extract_layout_text_pdfium(Path("missing.pdf"), DEFAULT_SECURITY_POLICY)

    def test_fake_textpage_reconstructs_visual_line_and_bbox(self):
        lines = _text_lines(FakeTextPage())
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].text, "A   B")
        self.assertEqual(lines[0].bbox, (10.0, 100.0, 33.0, 108.0))
        self.assertEqual(lines[1].text, "C")

    def test_render_budget_rejects_before_allocation(self):
        policy = SecurityPolicy(max_ocr_detail_pixels=100, max_ocr_image_bytes=400)
        with self.assertRaises(SecurityViolation):
            _check_render_budget(11, 10, policy)
        with self.assertRaises(SecurityViolation):
            _check_render_budget(10, 10, SecurityPolicy(max_ocr_detail_pixels=100, max_ocr_image_bytes=399))

    @unittest.skipUnless(importlib.util.find_spec("pypdfium2"), "pypdfium2 no instalado")
    def test_pdfium_text_render_and_crop_contract(self):
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            self.skipTest("reportlab no instalado")
        policy = SecurityPolicy(pdfium_enabled=True, pdfium_worker_enabled=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "fixture.pdf"
            writer = canvas.Canvas(str(pdf), pagesize=(612, 792))
            writer.drawString(100, 700, "TABLA DE CUBICACION")
            writer.drawString(100, 650, "1 Hormigon G35 31,40")
            writer.save()
            backend = PdfiumBackend.from_policy(policy)
            self.assertEqual(backend.page_count(pdf, policy=policy), 1)
            page = backend.extract_page(pdf, 1, policy=policy)
            self.assertIn("TABLA DE CUBICACION", page.text)
            self.assertIn("31,40", page.text)
            self.assertTrue(page.lines[0].bbox[2] > page.lines[0].bbox[0])
            candidates = locate_summary_tables(PdfText((page.text,)))
            self.assertTrue(candidates)
            self.assertIn("31,40", candidates[0].text)
            rendered = backend.render_page(pdf, 1, dpi=72, output=root / "page.png", policy=policy, workspace=root)
            self.assertEqual((rendered.width, rendered.height), (612, 792))
            cropped = backend.render_crop(pdf, 1, (100, 600, 300, 750), dpi=72,
                                          output=root / "crop.png", policy=policy, workspace=root)
            self.assertEqual((cropped.width, cropped.height), (200, 150))


if __name__ == "__main__":
    unittest.main()
