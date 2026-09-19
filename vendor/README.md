# Assets locales (no incluidos)

Este directorio no descarga ni contiene binarios/modelos. Un build controlado debe
incorporar Poppler, PaddleOCR, modelos, DLL y runtime, generar el inventario completo
`toolchain-manifest.json` y fijar su SHA-256 en el código/metadata del release.

Antes de distribuir, ejecute `verify_ocr_vendor.py`. Si falta, sobra o cambia un
archivo, OCR permanece desactivado. La procedencia y licencia
de cada asset deben revisarse fuera de este proceso.
