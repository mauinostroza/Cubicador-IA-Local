# Assets locales (no incluidos)

Este directorio no descarga ni contiene binarios/modelos. Un build controlado debe
incorporar Poppler, PaddleOCR, modelos, DLL y runtime, generar el inventario completo
`toolchain-manifest.json` (schema 2, versiones, tamaños y cierres de componentes) y
fijar su SHA-256 en el código/metadata del release. El sidecar opcional
`toolchain-manifest.sha256` se genera con `--write-pin` y no forma parte del payload.

Ejemplo de staging reproducible, ejecutado desde `backend`:

```text
python scripts/build_ocr_manifest.py --vendor ../vendor \
  --poppler-version 24.x --paddle-version 3.x --write --write-pin
```

El generador rechaza límites no positivos, archivos fuera de los grupos
`poppler`/`paddle`, enlaces y reparse points. El ejecutable final debe sustituir
`TRUSTED_TOOLCHAIN_MANIFEST_SHA256` mediante el proceso de release autorizado;
el valor cero mantiene OCR deshabilitado.

Antes de distribuir, ejecute `verify_ocr_vendor.py`. Si falta, sobra o cambia un
archivo, OCR permanece desactivado. La procedencia y licencia
de cada asset deben revisarse fuera de este proceso.
