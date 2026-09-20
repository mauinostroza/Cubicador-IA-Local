import json
import os
import struct
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import scripts.ocr_worker as worker
from cubicador.ocr import OcrError, PaddleOcrProvider
from cubicador.security import SecurityPolicy


def png(width: int = 8, height: int = 6) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    def chunk(name: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    return signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def request(root: Path, **limit_overrides) -> tuple[Path, Path, Path]:
    image = root / "page.png"; image.write_bytes(png())
    output = root / "result.json"
    limits = {"max_image_bytes": 10_000, "max_pixels": 1_000,
              "max_response_bytes": 10_000, "max_lines": 10,
              "max_text_chars": 1_000}
    limits.update(limit_overrides)
    path = root / "request.json"
    path.write_text(json.dumps({"schema": 1, "operation": "recognize", "job_id": "a" * 32,
        "image": str(image), "output": str(output), "limits": limits}), "utf-8")
    return path, image, output


class OcrWorkerTests(unittest.TestCase):
    def test_protocol_with_injected_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); req, _image, output = request(root)
            worker.run_request(req, root, lambda _p, w, h: {"lines": [
                {"text": "31,40", "bbox": [1, 1, w - 1, h - 1], "confidence": .98}
            ]})
            result = json.loads(output.read_text("utf-8"))
            self.assertEqual((result["width"], result["height"]), (8, 6))
            self.assertEqual(result["lines"][0]["text"], "31,40")

    def test_production_backend_fails_closed_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); req, _image, output = request(root)
            with self.assertRaises(worker.WorkerError):
                worker.run_request(req, root)
            self.assertFalse(output.exists())

    def test_rejects_paths_outside_workspace_and_reserved_output(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            root = Path(directory).resolve(); req, _image, output = request(root)
            outside_image = Path(outside).resolve() / "x.png"; outside_image.write_bytes(png())
            raw = json.loads(req.read_text("utf-8")); raw["image"] = str(outside_image)
            req.write_text(json.dumps(raw), "utf-8")
            with self.assertRaises(worker.WorkerError): worker.run_request(req, root, lambda *_: {"lines": []})
            req, _image, output = request(root); output.write_text("reserved", "utf-8")
            with self.assertRaises(worker.WorkerError): worker.run_request(req, root, lambda *_: {"lines": []})
            self.assertEqual(output.read_text("utf-8"), "reserved")

    def test_rejects_pixel_text_and_geometry_quotas(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); req, _image, _output = request(root, max_pixels=1)
            with self.assertRaises(worker.WorkerError): worker.run_request(req, root, lambda *_: {"lines": []})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); req, _image, _output = request(root, max_text_chars=2)
            with self.assertRaises(worker.WorkerError):
                worker.run_request(req, root, lambda *_: {"lines": [{"text": "larga", "bbox": [0, 0, 1, 1], "confidence": 1}]})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); req, _image, _output = request(root)
            with self.assertRaises(worker.WorkerError):
                worker.run_request(req, root, lambda *_: {"lines": [{"text": "x", "bbox": [-1, 0, 1, 1], "confidence": 1}]})

    def test_rejects_png_with_valid_header_but_corrupt_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); req, image, output = request(root)
            image.write_bytes(image.read_bytes()[:40])
            with self.assertRaises(worker.WorkerError):
                worker.run_request(req, root, lambda *_: {"lines": []})
            self.assertFalse(output.exists())

    def test_entrypoint_emits_nothing_and_keeps_output_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); req, _image, output = request(root)
            self.assertEqual(worker.main(["--request", str(req), "--workspace", str(root)]), 1)
            self.assertFalse(output.exists())

    def test_strict_json_rejects_duplicate_keys_and_nonfinite_numbers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); req, _image, output = request(root)
            text = req.read_text("utf-8").replace('"schema": 1,', '"schema": 1,"schema": 1,')
            req.write_text(text, "utf-8")
            with self.assertRaises(worker.WorkerError): worker.run_request(req, root, lambda *_: {"lines": []})
            self.assertFalse(output.exists())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); req, _image, output = request(root)
            with self.assertRaises(worker.WorkerError):
                worker.run_request(req, root, lambda *_: {"lines": [{"text": "x", "bbox": [0, 0, 1, 1], "confidence": float("nan")}]})
            self.assertFalse(output.exists())

    def test_hardlinks_and_failed_atomic_publish_are_rejected_or_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); req, image, output = request(root)
            linked = root / "linked.png"
            try: os.link(image, linked)
            except (OSError, NotImplementedError): self.skipTest("hardlinks no disponibles")
            raw = json.loads(req.read_text("utf-8")); raw["image"] = str(linked); req.write_text(json.dumps(raw), "utf-8")
            with self.assertRaises(worker.WorkerError): worker.run_request(req, root, lambda *_: {"lines": []})
            self.assertFalse(output.exists())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); req, _image, output = request(root)
            with patch("scripts.ocr_worker.os.replace", side_effect=OSError("fixture")):
                with self.assertRaises(OSError): worker.run_request(req, root, lambda *_: {"lines": []})
            self.assertFalse(output.exists())
            self.assertEqual(list(root.glob("*.tmp")), [])

    def test_worker_has_no_network_or_full_paddle_imports_and_gates_stay_closed(self):
        source = Path(worker.__file__).read_text("utf-8")
        for forbidden in ("import socket", "import urllib", "import requests", "import paddleocr", "import paddlex"):
            self.assertNotIn(forbidden, source.lower())
        lock = json.loads((Path(__file__).parents[1] / "ocr-release.lock.json").read_text("utf-8"))
        self.assertIs(lock["release_enabled"], False)
        self.assertTrue(all(value is False for value in lock["feature_gates"].values()))


class ProviderProtocolTests(unittest.TestCase):
    @staticmethod
    def provider(root: Path) -> PaddleOcrProvider:
        provider = object.__new__(PaddleOcrProvider)
        provider.runner = Path(__import__("sys").executable).resolve(); provider.model_root = root
        provider.toolchain = unittest.mock.Mock(); provider.toolchain.vendor_root = root
        provider.toolchain.verify_and_resolve.return_value = provider.runner
        provider.toolchain.launch_verifier.return_value = lambda: provider.runner
        return provider

    def test_provider_uses_supervised_json_protocol_and_rejects_missing_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); image = root / "page.png"; image.write_bytes(png())
            provider = self.provider(root)
            provider.toolchain.verify_and_resolve.return_value = provider.runner
            provider.toolchain.launch_verifier.return_value = lambda: provider.runner
            with patch("cubicador.ocr.run_command") as run:
                run.return_value = __import__("subprocess").CompletedProcess([], 1, b"", b"")
                cancel = threading.Event()
                with self.assertRaises(OcrError):
                    provider.recognize(image, policy=SecurityPolicy(), workspace=root, cancel=cancel)
                argv = run.call_args.args[0]
                self.assertEqual(argv[1], "--request")
                self.assertEqual(argv[3:], ["--workspace", str(root)])
                payload = json.loads(Path(argv[2]).read_text("utf-8"))
                self.assertEqual(set(payload), {"schema", "operation", "job_id", "image", "output", "limits"})
                self.assertIs(run.call_args.kwargs["cancel"], cancel)
                self.assertEqual(run.call_args.kwargs["timeout"], SecurityPolicy().model_timeout_seconds)

    def test_parent_rejects_nonce_replay_false_dimensions_and_bad_types(self):
        cases = ("nonce", "dimensions", "bool", "string", "nan", "inf", "duplicate", "empty", "partial")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve(); image = root / "page.png"; image.write_bytes(png())
                provider = self.provider(root)
                def fake_run(argv, **_kwargs):
                    payload = json.loads(Path(argv[2]).read_text("utf-8")); output = Path(payload["output"])
                    if case == "empty": output.write_bytes(b"")
                    elif case == "partial": output.write_text('{"schema":1', "utf-8")
                    elif case == "duplicate": output.write_text('{"schema":1,"schema":1,"ok":true,"job_id":"x","width":8,"height":6,"lines":[]}', "utf-8")
                    else:
                        response = {"schema": 1, "ok": True, "job_id": payload["job_id"], "width": 8, "height": 6,
                                    "lines": [{"text": "x", "x0": 0, "y0": 0, "x1": 1, "y1": 1, "confidence": .9}]}
                        if case == "nonce": response["job_id"] = "b" * 32
                        elif case == "dimensions": response["width"] = 9
                        elif case == "bool": response["lines"][0]["x0"] = False
                        elif case == "string": response["lines"][0]["confidence"] = "0.9"
                        elif case == "nan": response["lines"][0]["confidence"] = float("nan")
                        elif case == "inf": response["lines"][0]["confidence"] = float("inf")
                        output.write_text(json.dumps(response), "utf-8")
                    return __import__("subprocess").CompletedProcess(argv, 0, b"", b"")
                with patch("cubicador.ocr.run_command", side_effect=fake_run):
                    with self.assertRaises((OcrError, ValueError)):
                        provider.recognize(image, policy=SecurityPolicy(), workspace=root)


if __name__ == "__main__":
    unittest.main()
