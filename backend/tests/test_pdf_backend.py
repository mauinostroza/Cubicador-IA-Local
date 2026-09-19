import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cubicador.pdf_backend import (
    PdfiumBackend, PdfiumUnavailable, PdfBackendError, _check_render_budget,
    _text_lines,
)
from cubicador.pdf_text import PdfTextError, extract_layout_text_pdfium, _pdfium_response
from cubicador.pdf_text import PdfText
from cubicador.locator import locate_summary_tables
from cubicador.pipeline import process_pdf
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
    def test_api_import_does_not_load_pdfium(self):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        code = "import sys; import cubicador.http; assert 'pypdfium2' not in sys.modules"
        completed = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)

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

    def test_worker_protocol_rejects_extra_keys_and_invalid_geometry(self):
        policy = SecurityPolicy(pdfium_enabled=True, pdfium_worker_enabled=True)
        base_page = {"page_number": 1, "width_points": 100.0, "height_points": 100.0, "rotation": 0,
                     "bbox": [0, 0, 100, 100], "text": "x", "lines": []}
        with self.assertRaises(PdfTextError):
            _pdfium_response({"schema": 1, "ok": True, "engine": "PDFium", "pages": [base_page],
                              "unexpected": 1}, policy)
        for page in ({**base_page, "unexpected": 1},
                     {**base_page, "lines": [{"text": "x", "bbox": [0, 0, float("nan"), 1], "cells": []}]},
                     {**base_page, "lines": [{"text": "x", "bbox": [0, 0, 10, 10], "cells": [{"text": "x", "bbox": [0, 0, 11, 10]}]}]}):
            with self.subTest(page=page), self.assertRaises(PdfTextError):
                _pdfium_response({"schema": 1, "ok": True, "engine": "PDFium", "pages": [page]}, policy)

    @unittest.skipUnless(importlib.util.find_spec("pypdfium2"), "pypdfium2 no instalado")
    def test_worker_cancel_is_rejected_before_child_launch(self):
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            self.skipTest("reportlab no instalado")
        policy = SecurityPolicy(pdfium_enabled=True, pdfium_worker_enabled=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); pdf = root / "cancel.pdf"
            writer = canvas.Canvas(str(pdf)); writer.drawString(20, 700, "TABLA DE CUBICACION"); writer.save()
            cancel = __import__("threading").Event(); cancel.set()
            from cubicador.runtime import CancelledError
            with self.assertRaises(CancelledError):
                extract_layout_text_pdfium(pdf, policy, root, cancel=cancel)

    @unittest.skipUnless(importlib.util.find_spec("pypdfium2"), "pypdfium2 no instalado")
    def test_worker_reserved_output_cannot_be_preexisting_or_linked(self):
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            self.skipTest("reportlab no instalado")
        policy = SecurityPolicy(pdfium_enabled=True, pdfium_worker_enabled=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); pdf = root / "reserved.pdf"
            writer = canvas.Canvas(str(pdf)); writer.drawString(20, 700, "TABLA DE CUBICACION"); writer.save()
            (root / "pdfium-response.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(PdfTextError):
                extract_layout_text_pdfium(pdf, policy, root)

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

    @unittest.skipUnless(importlib.util.find_spec("pypdfium2"), "pypdfium2 no instalado")
    def test_worker_reconstructs_two_quantity_rows_and_geometry(self):
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            self.skipTest("reportlab no instalado")
        policy = SecurityPolicy(pdfium_enabled=True, pdfium_worker_enabled=True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf = root / "tabla.pdf"
            writer = canvas.Canvas(str(pdf), pagesize=(612, 792))
            writer.drawString(80, 760, "CAJETIN DEL PLANO")
            writer.drawString(100, 700, "TABLA DE CUBICACION")
            writer.drawString(100, 650, "Item    Descripcion       Unidad    Cantidad")
            writer.drawString(100, 625, "1       Hormigon G35      m3        31,40")
            writer.drawString(100, 600, "2       Emplantillado G10  m3        2,18")
            writer.save()
            document = extract_layout_text_pdfium(pdf, policy, root)
            self.assertIn("2  Emplantillado G10  m3  2,18", document.full_text)
            result = process_pdf(pdf, output_root=root, policy=policy)
            self.assertEqual([q.numeric_value for row in result.filas for q in row.quantities], [31.40, 2.18])
            self.assertEqual([row.quantities[0].bbox is not None for row in result.filas], [True, True])
            self.assertEqual(result.extractor_version, "pdfium-worker")

    @unittest.skipUnless(importlib.util.find_spec("pypdfium2"), "pypdfium2 no instalado")
    def test_cropbox_and_all_page_rotations_keep_crop_content(self):
        try:
            from reportlab.pdfgen import canvas
            from pypdf import PdfReader, PdfWriter
            from PIL import Image
        except ImportError:
            self.skipTest("dependencia de PDF no instalada")
        policy = SecurityPolicy(pdfium_enabled=True, pdfium_worker_enabled=True,
                                max_ocr_detail_pixels=1_000_000, max_ocr_image_bytes=4_000_000)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            writer = canvas.Canvas(str(source), pagesize=(300, 200))
            writer.setFillColorRGB(0, 0, 0)
            writer.rect(10, 10, 50, 30, fill=1, stroke=0)
            writer.save()
            for rotation in (0, 90, 180, 270):
                rotated = root / f"rotation-{rotation}.pdf"
                reader = PdfReader(str(source)); page = reader.pages[0]
                page.cropbox.lower_left = (5, 5); page.cropbox.upper_right = (295, 195)
                page.rotate(rotation)
                output = PdfWriter(); output.add_page(page)
                with rotated.open("wb") as stream: output.write(stream)
                backend = PdfiumBackend.from_policy(policy)
                rendered = backend.render_crop(rotated, 1, (10, 10, 60, 40), dpi=72,
                                               output=root / f"crop-{rotation}.png", policy=policy, workspace=root)
                self.assertEqual((rendered.width, rendered.height), ((30, 50) if rotation in (90, 270) else (50, 30)))
                with Image.open(rendered.output) as image:
                    self.assertGreater(sum(1 for pixel in image.convert("L").getdata() if pixel < 100), 20)


if __name__ == "__main__":
    unittest.main()
