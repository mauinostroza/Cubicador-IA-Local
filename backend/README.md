# Backend piloto

Procesa únicamente PDF con capa de texto. No contiene OCR, visión, APIs externas ni cálculo geométrico.

```bash
cd backend
python -m pip install -e .
cubicador plano.pdf --excel cantidades.xlsx --json cantidades.json --text plano_layout.txt
```

El Excel incluye `Cantidades`, `Pendientes` y `Trazabilidad`; cada fila conserva el valor original y la línea fuente. Por defecto usa el parser determinista offline. Para interpretar layouts atípicos con un modelo local servido por `llama.cpp`:

```bash
cubicador plano.pdf --excel cantidades.xlsx --mode llama-server \
  --llama-url http://127.0.0.1:8080/v1/chat/completions
```

Pruebas (el PDF se genera en un directorio temporal):

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

El adaptador `llama-server` es experimental y todavía no ha sido validado con un modelo concreto. FastAPI, SQLite y el empaquetado del modelo y del EXE quedan pendientes de las siguientes etapas.
