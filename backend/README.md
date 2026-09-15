# Backend piloto

> En Windows, los subprocesos se crean suspendidos, se asignan a un Job Object
> con límites y recién entonces se reanudan. Antes de distribuir el EXE debe
> aprobarse `scripts/windows_job_smoke.py` y el workflow Windows. El feature gate
> permanece desactivado por defecto y solo el smoke lo habilita explícitamente.

Procesa únicamente PDF con capa de texto. No contiene OCR, visión, APIs externas ni cálculo geométrico.

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
