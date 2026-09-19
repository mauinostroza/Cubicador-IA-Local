# NOTICE inputs (release pending)

The following notices must be collected and checked into the final release
bundle before PDFium or PaddleOCR is enabled:

- `pypdfium2`: upstream package license and any bundled runtime notice.
- PDFium: Chromium/PDFium BSD notice and third-party notices for the exact
  binary build.
- PaddleOCR: Apache-2.0 license and upstream NOTICE, if supplied by the exact
  source revision.
- PP-OCRv5 mobile models: license/README from each exact model artifact.

This file is a checklist, not a replacement for the upstream notices. The
release gate must fail while the exact notices are absent or marked
`NOASSERTION`.
