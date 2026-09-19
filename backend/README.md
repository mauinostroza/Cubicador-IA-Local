# Backend piloto

> En Windows, los subprocesos se crean suspendidos, se asignan a un Job Object
> con límites y recién entonces se reanudan. Antes de distribuir el EXE debe
> aprobarse `scripts/windows_job_smoke.py` y el workflow Windows. La validación
> Windows fue aprobada en CI antes de habilitar Job Objects por defecto.

Procesa PDF con capa de texto y dispone de un contrato OCR local opcional. No contiene
APIs externas ni cálculo geométrico.

## OCR local opcional (P2)

Cuando el PDF contiene poco texto y no aparece el título, `process_pdf(...,
ocr_provider=...)` puede activar OCR. La etapa está cerrada por defecto: no descarga
modelos ni usa red. Busca como máximo en 5 páginas a 150 DPI (16 MP por página),
recorta una sola tabla y la renderiza a 300 DPI (12 MP) con `pdftoppm`
empaquetado y solo acepta texto bajo `TABLA DE CUBICACIÓN` o `CUADRO DE
CUBICACIÓN`. `PaddleOcrProvider` exige un runner y modelos preinstalados dentro de
`vendor`; Qwen no está habilitado.

Esta etapa deja preparada y probada la integración, pero no constituye una prueba
end-to-end hasta empaquetar un runner PaddleOCR, sus modelos, el manifest firmado
por hash confiable y la regla de Windows Firewall que bloquee su tráfico.

La integración aún no está lista para activación de usuario. `--ocr` solo funciona
cuando el build oficial reemplaza el pin deshabilitado del toolchain y empaqueta el
inventario exacto de Poppler, Paddle, modelos, DLL y runtime. Ante archivos ausentes,
adicionales o hashes distintos, falla antes de ejecutar. Los scripts de construcción
y verificación no descargan contenido. El modo de desarrollo que admite `/usr/bin`
es explícito y no forma parte del release Windows.

```bash
cd backend
python -m pip install -e .
cubicador plano.pdf --output-dir ./salidas --excel ./salidas/cantidades.xlsx \
  --json ./salidas/cantidades.json --text ./salidas/plano_layout.txt
```

El Excel incluye `Cantidades`, `Pendientes` y `Trazabilidad`; cada fila conserva el valor original y la línea fuente. Por defecto usa el parser determinista offline. Para interpretar layouts atípicos con un modelo local servido por `llama.cpp`:

```bash
cubicador plano.pdf --excel cantidades.xlsx --mode llama-server \
  --output-dir ./salidas
```

Pruebas (el PDF se genera en un directorio temporal):

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

El adaptador `llama-server` es experimental y todavía no ha sido validado con un modelo concreto. FastAPI, SQLite y el empaquetado del modelo y del EXE quedan pendientes de las siguientes etapas.
