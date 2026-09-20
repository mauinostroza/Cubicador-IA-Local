# NOTICE inputs (release pending)

The staging workflow extracts license files from each verified wheel into its
evidence artifact. Nothing in this document enables a runtime feature gate.

Verified upstream package inputs currently pinned:

- `pypdfium2` 5.3.0 Windows x64: BSD-3-Clause, Apache-2.0, PDFium and the
  bundled third-party notices included by the wheel.
- `paddlepaddle` 3.2.0 CPython 3.12 Windows x64: Apache-2.0.
- PP-OCRv5 mobile detection and `latin_PP-OCRv5_mobile_rec` recognition: eight
  direct files from immutable Hugging Face commits, each pinned by SHA-256 and size: Apache-2.0. These are
  assembly inputs, not PyPI staging `build_inputs`.

Reference-only packages, explicitly excluded from the release payload because
their standard pipelines include download/network paths:

- `paddleocr` 3.2.0: Apache-2.0 (reference only; not staged or shipped).
- `paddlex` 3.2.1: Apache-2.0 (reference only; not staged or shipped).

The exact wheel license files are preserved in CI staging. Before production
activation they must be copied into the final distribution and reviewed.

Blocked inputs:

- The separate `pdfium_binary` asset is blocked. PDFium is embedded in the
  verified pypdfium2 wheel, but no independent Windows artifact with an
  upstream SHA-256 and build provenance is fixed here; the wheel's bundled
  notices are the only accepted provenance at this checkpoint.
- `paddle_ocr_runner` is blocked because PaddleOCR does not publish the
  required official Windows runner executable.
- PP-OCRv5 tarballs remain blocked and prohibited. The accepted alternative is
  the eight individually pinned upstream files. Recognition configuration
  embeds its character dictionary; there is no second dictionary artifact.

This is an input/staging checklist, not a complete transitive SBOM and not a
replacement for the upstream notices. The
release gate must remain closed while blocked inputs or exact notices are
unresolved.
