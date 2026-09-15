import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cubicador.ocr import OcrError, OcrLine, OcrPage, PaddleOcrProvider, _grid_table_bounds, _transform_box, ocr_document
from cubicador.security import SecurityPolicy, SecurityViolation
from cubicador.pdf_text import PdfText
from cubicador.pipeline import _match_quantity_cell, process_pdf


class FakeProvider:
    def __init__(self, pages): self.pages = iter(pages)
    def recognize(self, image_path, **kwargs): return next(self.pages)


class OcrTests(unittest.TestCase):
    def test_quantity_cell_requires_a_unique_exact_match(self):
        cells = [
            {"text": "2", "bbox": (1, 1, 2, 2), "confidence": .99},
            {"text": "2", "bbox": (10, 1, 12, 2), "confidence": .98},
            {"text": "2,18", "bbox": (20, 1, 24, 2), "confidence": .97},
        ]
        self.assertIsNone(_match_quantity_cell("2", cells))
        self.assertEqual(_match_quantity_cell(" 2,18 ", cells)["bbox"], (20, 1, 24, 2))
        self.assertIsNone(_match_quantity_cell("18", cells))

    def test_recognizes_only_expected_title_region(self):
        page = OcrPage(1000, 1000, (
            OcrLine("CAJETIN", 0, 10, 100, 30, .99),
            OcrLine("TABLA DE CUBICACIÓN", 50, 200, 400, 230, .99),
            OcrLine("Item Descripción Unidad Cantidad", 50, 240, 700, 270, .98),
            OcrLine("1 Hormigón m3 31,40", 50, 280, 600, 310, .97),
        ))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image = root / "ocr-page.png"; image.write_bytes(b"png")
            cropped = OcrPage(1000, 800, page.lines[1:])
            with patch("cubicador.ocr.render_pages", return_value=(image,)), patch("cubicador.ocr._render_crop", return_value=image), patch("cubicador.ocr._png_size", return_value=(1000, 1000)), patch("cubicador.ocr._grid_table_bounds", return_value=(40, 190, 700, 200)), patch("cubicador.ocr._page_size_points", return_value=(720, 720)):
                result = ocr_document(root / "x.pdf", 1, FakeProvider([page, cropped]), SecurityPolicy(), root)
        self.assertNotIn("CAJETIN", result.pages[0])
        self.assertIn("TABLA DE CUBICACIÓN", result.pages[0])
        self.assertIn("31,40", result.pages[0])

    def test_rejects_unexpected_title_and_pixel_bomb(self):
        no_title = OcrPage(100, 100, (OcrLine("LISTADO GENERAL", 0, 0, 20, 20, .9),))
        bomb = OcrPage(100_000, 100_000, (OcrLine("CUADRO DE CUBICACIÓN", 0, 0, 20, 20, .9),))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image = root / "x.png"; image.write_bytes(b"x")
            with patch("cubicador.ocr.render_pages", return_value=(image,)), patch("cubicador.ocr._png_size", return_value=(100, 100)):
                with self.assertRaises(OcrError): ocr_document(root / "x.pdf", 1, FakeProvider([no_title]), SecurityPolicy(), root)
            with patch("cubicador.ocr.render_pages", return_value=(image,)), patch("cubicador.ocr._png_size", return_value=(100, 100)):
                with self.assertRaises(SecurityViolation): ocr_document(root / "x.pdf", 1, FakeProvider([bomb]), SecurityPolicy(), root)

    def test_paddle_adapter_requires_vendor_assets(self):
        with self.assertRaises((SecurityViolation, OcrError, FileNotFoundError)):
            PaddleOcrProvider("/tmp/runner", "/tmp/models", trusted_manifest_sha256="0"*64)

    def test_policy_rejects_invalid_ocr_limits(self):
        for values in ({"max_ocr_pages": 0}, {"ocr_search_dpi": 0}, {"max_ocr_search_pixels": 0}):
            with self.subTest(values=values), self.assertRaises(ValueError): SecurityPolicy(**values)

    def test_pipeline_uses_ocr_only_for_low_text_fallback(self):
        ocr_text = PdfText(("TABLA DE CUBICACIÓN\nItem  Descripcion  Unidad  Cantidad\n1  Hormigon G35  m3  31,40\n2  Emplantillado G10  m3  2,18",))
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "plano.pdf"; pdf.write_bytes(b"%PDF-test")
            provider = FakeProvider([])
            with patch("cubicador.pipeline.extract_layout_text", return_value=PdfText(("CAJETIN",))), \
                 patch("cubicador.pipeline.ocr_document", return_value=ocr_text) as fallback:
                result = process_pdf(pdf, ocr_provider=provider)
        fallback.assert_called_once()
        self.assertTrue(result.tabla_encontrada)
        self.assertEqual(result.extractor_version, "ocr-local")
        self.assertEqual(result.filas[0].quantities[0].numeric_value, 31.4)
        self.assertEqual(result.filas[1].quantities[0].numeric_value, 2.18)

    def test_pipeline_recovers_when_pdf_has_no_text(self):
        from cubicador.pdf_text import NoTextPdfError
        ocr_text = PdfText(("CUADRO DE CUBICACIÓN\nItem  Descripcion  Unidad  Cantidad\n1  H  m3  1,0",))
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "plano.pdf"; pdf.write_bytes(b"%PDF-test")
            with patch("cubicador.pipeline.extract_layout_text", side_effect=NoTextPdfError("El PDF no contiene texto legible")), \
                 patch("cubicador.pipeline.get_page_count", return_value=1), \
                 patch("cubicador.pipeline.ocr_document", return_value=ocr_text):
                result = process_pdf(pdf, ocr_provider=FakeProvider([]))
        self.assertTrue(result.tabla_encontrada)

    def test_corrupt_provider_output_is_rejected(self):
        corrupt = OcrPage(100, 100, (OcrLine("TABLA DE CUBICACIÓN", -1, 0, 20, 20, 1.2),))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); image = root / "x.png"; image.write_bytes(b"x")
            with patch("cubicador.ocr.render_pages", return_value=(image,)), patch("cubicador.ocr._png_size", return_value=(100, 100)):
                with self.assertRaises((OcrError, SecurityViolation)):
                    ocr_document(root / "x.pdf", 1, FakeProvider([corrupt]), SecurityPolicy(), root)

    def test_grid_bounds_excludes_distant_drawing(self):
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "grid.png"
            canvas = Image.new("L", (1000, 700), 255); draw = ImageDraw.Draw(canvas)
            draw.rectangle((650, 60, 950, 230), outline=0, width=3)
            for y in (100, 140, 180): draw.line((650, y, 950, y), fill=0, width=2)
            for x in (700, 850): draw.line((x, 60, x, 230), fill=0, width=2)
            # Dibujo vecino alineado bajo la tabla: no debe extender el recorte.
            draw.rectangle((620, 360, 980, 650), outline=0, width=4); canvas.save(image)
            title = OcrLine("CUADRO DE CUBICACIÓN", 710, 70, 890, 90, .99)
            bounds = _grid_table_bounds(image, title, OcrPage(1000, 700, (title,)))
        self.assertIsNotNone(bounds)
        self.assertGreater(bounds[0], 600)
        self.assertLess(bounds[0] + bounds[2], 980)
        self.assertLess(bounds[1] + bounds[3], 280)

    def test_a1_scaled_search_coordinates_transform_exactly(self):
        transformed = _transform_box((3200, 100, 700, 300), (4000, 2825), (2383.94, 1683.78), 300)
        self.assertEqual(transformed, (7946, 248, 1738, 745))

    def test_policy_rejects_incoherent_ocr_budget(self):
        with self.assertRaises(ValueError):
            SecurityPolicy(max_ocr_calls=5)
        with self.assertRaises(ValueError):
            SecurityPolicy(max_ocr_total_pixels=80_000_000)


if __name__ == "__main__": unittest.main()
