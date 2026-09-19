# Cubicador IA Local

Aplicación offline para localizar exclusivamente `TABLA DE CUBICACIÓN` o
`CUADRO DE CUBICACIÓN` en planos PDF, conservar evidencia y exportar las
cantidades a XLSX.

## Estado

- Motor determinista PDF → tabla → JSON/XLSX.
- Fallback OCR preparado, pero bloqueado hasta instalar y validar el payload
  firmado de Poppler/PaddleOCR.
- API Python local en `127.0.0.1`, con token efímero, cola acotada,
  cancelación y limpieza automática.
- Interfaz Next.js local para cargar, revisar y descargar resultados.
- 49 pruebas backend y smoke offline del contrato de release aprobados.
- No existen proveedores, descargas ni APIs externas en ejecución.

El EXE final continúa bloqueado hasta aprobar firma Authenticode, ACL,
firewall/aislamiento, sustitución concurrente y E2E en Windows con el payload
real.

## Backend

```bash
cd backend
python -m pip install -e ".[test]"
export CUBICADOR_API_TOKEN="token-aleatorio-de-al-menos-32-caracteres"
cubicador-local-api --root ./trabajos-locales
```

En Windows, defina `CUBICADOR_API_TOKEN` con PowerShell o desde el launcher
empacado. El token no debe escribirse en el frontend ni enviarse al navegador.

## Interfaz

Use el mismo token exclusivamente en el proceso local de Next:

```bash
npm install
CUBICADOR_BACKEND_TOKEN="$CUBICADOR_API_TOKEN" npm run dev
```

La interfaz escucha en `127.0.0.1:3000`. Su proxy solo puede comunicarse con
`http://127.0.0.1:8765`; el navegador nunca recibe el token del backend.

## Pruebas

```bash
cd backend
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python scripts/windows_vendor_smoke.py
```

## Flujo

1. Python intenta `pdftotext -layout` preservando páginas y columnas.
2. Si el PDF contiene texto CAD dibujado, el release podrá usar OCR local sobre
   un recorte acotado de la tabla.
3. Python valida números, unidades, ambigüedades y evidencia.
4. Se genera XLSX con `Cantidades`, `Pendientes` y `Trazabilidad`.

Qwen3-VL no está habilitado. Solo se evaluará como respaldo, con una llamada
local sin herramientas ni red, si el benchmark de PaddleOCR demuestra que es
necesario.

## Migración PDFium

Existe un scaffold cerrado para reemplazar Poppler por PDFium/pypdfium2 y
evitar incorporar Poppler al paquete distribuible. Reconoce páginas, texto,
coordenadas y render dentro de un proceso hijo supervisado. Las pruebas E2E
recuperan `31,40` y `2,18` con evidencia de celdas, además de CropBox y
rotaciones. Sus gates permanecen apagados y no se activarán hasta disponer del
runtime firmado, ACL protegidas y el payload OCR Windows validado.
