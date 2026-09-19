# SPDX inputs for the pending offline OCR release

This file is an input checklist, not a declaration that a release payload is
currently present. The release remains disabled until every artifact has an
exact version, official source URL, build provenance, and independently
verified SHA-256/size.

| Component | Intended artifact | SPDX candidates | Required provenance |
|---|---|---|---|
| PDFium binding | `pypdfium2` Windows x64 wheel | `BSD-3-Clause`, `Apache-2.0` | Exact wheel filename/version and upstream release record |
| PDFium runtime | PDFium binary shipped by the selected binding | `BSD-3-Clause` | PDFium revision, build flags, compiler/toolchain and vendor notice |
| OCR runtime | PaddleOCR runner, CPU/mobile profile | `Apache-2.0` | Exact source commit/release and build record |
| OCR models | PP-OCRv5 mobile detection/recognition models | `Apache-2.0` (to be confirmed from each model artifact) | Exact model files, source URL, version and license notice |
| Legacy development tool | Poppler, never in release payload | `GPL-2.0-or-later` | Local developer installation only |

Hashes and sizes are intentionally not included here. They must be obtained
from the final, reviewed release inputs; a locally computed hash is not a
substitute for provenance.
