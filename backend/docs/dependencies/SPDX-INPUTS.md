# SPDX inputs for the pending offline OCR release

This file is an input checklist, not a declaration that a release payload is
currently present. The release remains disabled until every artifact has an
exact version, official source URL, build provenance, and independently
verified SHA-256/size.

| Component | Intended artifact | SPDX candidates | Required provenance |
|---|---|---|---|
| PDFium binding | `pypdfium2` 5.3.0 Windows x64 wheel | `BSD-3-Clause`, `Apache-2.0` | PyPI JSON record and pinned wheel SHA-256 in `ocr-release.lock.json` |
| PDFium runtime | Embedded `pypdfium2_raw/pdfium.dll` | `BSD-3-Clause` plus bundled notices | Derived only from the verified wheel; independent asset remains blocked |
| Paddle runtime | `paddlepaddle` 3.2.0 CPython 3.12 Windows x64 wheel | `Apache-2.0` | PyPI JSON record and pinned wheel SHA-256 |
| OCR package references | `paddleocr` 3.2.0 + `paddlex` 3.2.1 wheels | `Apache-2.0` | **EXCLUDED** from payload due download/network paths; hashes retained only for audit |
| OCR runner | PaddleOCR CPU/mobile Windows runner executable | `Apache-2.0` | **BLOCKED**: no official runner artifact for this contract |
| OCR models | PP-OCRv5 mobile detection/recognition models | `Apache-2.0` pending exact artifact | **BLOCKED**: official URLs exist, no upstream SHA-256 |
| Legacy development tool | Poppler, never in release payload | `GPL-2.0-or-later` | Local developer installation only |

Hashes, sizes and immutable URLs for verified wheels are recorded in
`ocr-release.lock.json`; the CI staging workflow obtains them only from PyPI
and verifies them before extraction. A locally computed hash is not a
substitute for provenance. Blocked assets retain null hash/size fields.
This checklist and the staging SBOM do not claim complete transitive dependency
coverage; production activation requires a final SBOM generated from the exact
assembled payload.
