import hashlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import zipfile
import sys
from urllib.request import Request, ProxyHandler

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

import build_ocr_staging
from build_ocr_staging import StagingError, _download_verified, _safe_extract_wheel, _RejectRedirect, build


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def geturl(self):
        return "https://files.pythonhosted.org/x/asset.whl"

    def read(self, _size=-1):
        value, self.payload = self.payload, b""
        return value


class StagingTests(unittest.TestCase):
    def test_download_checks_upstream_digest_before_replace(self):
        payload = b"verified wheel bytes"
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw) / "asset.whl"
            destination.write_bytes(b"old")
            artifact = {"url": "https://files.pythonhosted.org/x/asset.whl", "filename": "asset.whl",
                        "sha256": hashlib.sha256(b"wrong").hexdigest(), "size_bytes": len(payload)}
            with patch("build_ocr_staging._open_direct", return_value=_Response(payload)):
                with self.assertRaises(StagingError):
                    _download_verified(artifact, destination)
            self.assertEqual(destination.read_bytes(), b"old")

    def test_download_rejects_non_pypi_host_before_network(self):
        artifact = {"url": "https://example.invalid/asset.whl", "filename": "asset.whl",
                    "sha256": "0" * 64, "size_bytes": 1}
        with patch("build_ocr_staging._open_direct") as network:
            with self.assertRaises(StagingError):
                _download_verified(artifact, Path(tempfile.gettempdir()) / "asset.whl")
            network.assert_not_called()

    def test_redirect_handler_rejects_before_following(self):
        with self.assertRaises(StagingError):
            _RejectRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.invalid/payload")

    def test_direct_opener_disables_environment_proxy(self):
        captured = []

        class _Opener:
            def open(self, _request, *, timeout):
                self.timeout = timeout
                return _Response(b"")

        opener = _Opener()

        def fake_build_opener(*handlers):
            captured.extend(handlers)
            return opener

        with patch("build_ocr_staging.build_opener", side_effect=fake_build_opener):
            result = build_ocr_staging._open_direct(Request("https://files.pythonhosted.org/x"), timeout=7)
        self.assertIsInstance(captured[0], ProxyHandler)
        self.assertIsInstance(captured[1], _RejectRedirect)
        self.assertEqual(opener.timeout, 7)
        result.close = lambda: None

    def test_wheel_path_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); wheel = root / "bad.whl"; target = root / "out"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("../escaped.txt", b"bad")
            wheel.write_bytes(buffer.getvalue())
            with self.assertRaises(StagingError):
                _safe_extract_wheel(wheel, target)
            self.assertFalse((root.parent / "escaped.txt").exists())

    def test_wheel_extraction_keeps_license_and_package(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); wheel = root / "ok.whl"; target = root / "out"
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("demo/__init__.py", b"# no execution")
                archive.writestr("demo.dist-info/licenses/LICENSE", b"Apache-2.0")
            wheel.write_bytes(buffer.getvalue())
            files = _safe_extract_wheel(wheel, target)
            self.assertIn("demo/__init__.py", files)
            self.assertTrue((target / "demo.dist-info/licenses/LICENSE").is_file())

    def test_wheel_extraction_quota_is_enforced(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); wheel = root / "large.whl"; target = root / "out"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("demo/data.bin", b"12")
            with patch("build_ocr_staging.MAX_EXTRACTED_BYTES", 1):
                with self.assertRaises(StagingError):
                    _safe_extract_wheel(wheel, target)
            self.assertFalse(target.exists())

    def test_wheel_compression_bomb_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); wheel = root / "bomb.whl"; target = root / "out"
            with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("demo/repeated.bin", b"A" * 10_000)
            with patch("build_ocr_staging.MAX_COMPRESSION_RATIO", 2):
                with self.assertRaises(StagingError):
                    _safe_extract_wheel(wheel, target)

    def test_build_rejects_accumulated_download_quota_before_network(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            verified = {
                "status": "verified_upstream_pypi", "filename": "asset.bin",
                "url": "https://files.pythonhosted.org/x/asset.bin", "sha256": "0" * 64,
                "size_bytes": 6,
            }
            lock = {
                "release_enabled": False,
                "feature_gates": {"pdfium": False, "paddle_ocr": False, "poppler_payload": False},
                "artifacts": {"a": verified, "b": verified}, "build_inputs": ["a", "b"],
            }
            lock_path = root / "lock.json"; lock_path.write_text(json.dumps(lock), encoding="utf-8")
            with patch.dict(os.environ, {"CI": "true"}, clear=False), \
                    patch("build_ocr_staging.MAX_TOTAL_DOWNLOAD_BYTES", 10), \
                    patch("build_ocr_staging._download_verified") as download:
                with self.assertRaises(StagingError):
                    build(lock_path, root / "staging")
                download.assert_not_called()

    def test_status_preserves_excluded_reference_reason(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            lock = {
                "release_enabled": False,
                "feature_gates": {"pdfium": False, "paddle_ocr": False, "poppler_payload": False},
                "artifacts": {
                    "input": {"status": "verified_upstream_pypi", "filename": "asset.bin",
                              "url": "https://files.pythonhosted.org/x/asset.bin", "sha256": "0" * 64,
                              "size_bytes": 1},
                    "reference": {"status": "excluded_from_release_reference",
                                  "excluded_reason": "no forma parte del payload"},
                },
                "build_inputs": ["input"],
            }
            lock_path = root / "lock.json"; lock_path.write_text(json.dumps(lock), encoding="utf-8")
            record = {"filename": "asset.bin", "url": lock["artifacts"]["input"]["url"],
                      "sha256": "0" * 64, "size_bytes": 1}
            with patch.dict(os.environ, {"CI": "true"}, clear=False), \
                    patch("build_ocr_staging._download_verified", return_value=record):
                build(lock_path, root / "staging")
            status = json.loads((root / "staging" / "STAGING-STATUS.json").read_text("utf-8"))
            self.assertEqual(status["excluded_references"][0]["reason"], "no forma parte del payload")

    def test_build_rejects_blocked_input_before_network(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            lock = {
                "release_enabled": False,
                "feature_gates": {"pdfium": False, "paddle_ocr": False, "poppler_payload": False},
                "artifacts": {"models": {"status": "blocked_no_upstream_sha256"}},
                "build_inputs": ["models"],
            }
            lock_path = root / "lock.json"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            with patch.dict(os.environ, {"CI": "true"}, clear=False):
                with self.assertRaises(StagingError):
                    build(lock_path, root / "staging")


if __name__ == "__main__":
    unittest.main()
