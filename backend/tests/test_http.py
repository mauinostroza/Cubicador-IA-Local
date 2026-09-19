import io
import tempfile
import unittest
from email.message import Message
from pathlib import Path

from cubicador.http import ApiError, JobManager, _read_multipart, create_server
from cubicador.security import SecurityPolicy, SecurityViolation


class LocalHttpTests(unittest.TestCase):
    def test_server_requires_loopback_and_strong_token(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SecurityViolation):
                create_server("0.0.0.0", 0, root=directory, token="x" * 32)
            with self.assertRaises(SecurityViolation):
                create_server("127.0.0.1", 0, root=directory, token="short")

    def test_bounded_multipart_accepts_only_pdf_and_ocr(self):
        boundary = "----cubicador-test-boundary"
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"pdf\"; filename=\"plan.pdf\"\r\n"
            "Content-Type: application/pdf\r\n\r\n"
        ).encode() + b"%PDF-1.4\nfixture\n" + (
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"ocr\"\r\n\r\nfalse"
            f"\r\n--{boundary}--\r\n"
        ).encode()
        headers = Message()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        headers["Content-Length"] = str(len(body))
        content, filename, ocr = _read_multipart(io.BytesIO(body), headers, 1024)
        self.assertEqual(content, b"%PDF-1.4\nfixture\n")
        self.assertEqual(filename, "plan.pdf")
        self.assertFalse(ocr)

    def test_multipart_rejects_missing_length_and_oversize(self):
        headers = Message()
        headers["Content-Type"] = "multipart/form-data; boundary=x"
        with self.assertRaises(ApiError):
            _read_multipart(io.BytesIO(), headers, 16)
        headers["Content-Length"] = str(3 * 1024 * 1024)
        with self.assertRaises(ApiError):
            _read_multipart(io.BytesIO(), headers, 16)

    def test_manager_rejects_ocr_until_signed_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = JobManager(Path(directory), SecurityPolicy(max_queued_jobs=1))
            self.assertFalse(manager.text_ready)
            with self.assertRaises(ApiError) as raised:
                manager.submit(b"%PDF-1.4\nfixture", "plan.pdf", ocr=True)
            self.assertEqual(raised.exception.code, "OCR_NOT_READY")


if __name__ == "__main__":
    unittest.main()
