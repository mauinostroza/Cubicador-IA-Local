# Cubicador IA Local

Aplicación local para extraer exclusivamente tablas resumen de cantidades desde
planos PDF con texto seleccionable y exportarlas a Excel con trazabilidad.

## Estado

- Piloto determinista Python implementado y verificado: `backend/`.
- 8 pruebas unitarias/integradas aprobadas.
- Interfaz Next.js disponible como base visual; su API original todavía es
  heredada y será sustituida por FastAPI.
- Integración real con modelo local, persistencia y EXE portable Windows:
  pendientes de los siguientes hitos.

## Probar el backend

```bash
cd backend
python -m pip install -e .
cubicador plano.pdf --excel cantidades.xlsx --json cantidades.json
PYTHONPATH=src python -m unittest discover -s tests -v
```

El alcance excluye OCR, visión y cálculo de cantidades desde la geometría del
plano. Los casos ambiguos se marcan para revisión.

## Prototipo visual heredado

Sistema local para extracción y catalogación automática de datos en tablas de PDFs usando inteligencia artificial.

## Requisitos previos

- **Node.js 18+** y **bun** (o npm/pnpm/yarn)
- **Poppler** instalado en el sistema (proporciona `pdftotext` y `pdfinfo`):
  - **Ubuntu/Debian:** `sudo apt install poppler-utils`
  - **macOS:** `brew install poppler`
  - **Windows:** el paquete portable incluirá Poppler en `vendor/poppler/bin`; no se carga desde `PATH`

## Instalación

```bash
# 1. Instalar dependencias
bun install   # o: npm install

# 2. Configurar credenciales de IA

# 3. Verificar que pdftotext está disponible
pdftotext -v
```

## Ejecución en desarrollo

```bash
bun run dev   # o: npm run dev
```

Abrir `http://localhost:3000` en el navegador.

## Cómo usar

1. **Cargar PDF**: arrastra o selecciona un archivo PDF (máx 25 MB) con capa de texto
2. **Título de la tabla**: ingresa el nombre de la tabla/sección a buscar (ej: "Tabla 4.2 - Propiedades Mecánicas")
3. **Términos a catalogar**: lista los términos cuyos valores quieres extraer, uno por línea
4. **Pista de contexto** (opcional): información adicional para ayudar al modelo
5. **Click en "Extraer y Catalogar"** y esperar ~5 segundos
6. **Editar resultados**: las celdas son editables inline
7. **Exportar**: botones para CSV (Excel), copiar tabla al portapapeles, o copiar JSON

## Estructura del proyecto

```
src/
├── app/
│   ├── api/extract/route.ts    # Endpoint POST que procesa el PDF con IA
│   ├── layout.tsx              # Layout raíz con Toaster
│   ├── page.tsx                # Página principal
│   └── globals.css             # Estilos Tailwind
└── components/
    ├── extractor/
    │   ├── file-uploader.tsx       # Carga de PDF (drag & drop)
    │   ├── parameters-form.tsx     # Formulario de título y términos
    │   └── results-table.tsx       # Tabla editable + exportación
    └── ui/                          # Componentes shadcn/ui
```

## Arquitectura técnica

El flujo de procesamiento es:

```
PDF (binario)
    ↓
pdftotext -layout  →  texto plano preservando estructura
    ↓
LLM (z.ai SDK)  →  razona, ubica tabla, extrae valores
    ↓
JSON estructurado  →  tabla editable en la UI
```

- **Frontend**: Next.js 16 + TypeScript + Tailwind CSS 4 + shadcn/ui
- **Backend**: API Route de Next.js (runtime Node.js)
- **Extracción PDF**: `pdftotext` (Poppler) vía child_process
- **IA**: servidor local restringido a `127.0.0.1:8080`, sin proveedor externo
- **Validación**: temperatura 0.1 para máxima precisión, parsing robusto de JSON

## Limitaciones conocidas

- **PDFs escaneados**: si el PDF no tiene capa de texto (es imagen), `pdftotext` no extrae nada. Para esos casos se requeriría integrar OCR (Tesseract).
- **Tamaño máximo**: 25 MB por PDF
- **Contexto del LLM**: si el PDF excede 60.000 caracteres, se trunca (puede perder tablas al final del documento)

## Archivos relevantes

- `src/app/api/extract/route.ts` — Lógica de extracción + prompt de IA
- `src/app/page.tsx` — Interfaz principal
- `src/components/extractor/*.tsx` — Componentes modulares
- `package.json` — Dependencias y scripts

## Scripts disponibles

```bash
bun run dev        # Servidor de desarrollo
bun run build      # Build de producción
bun run start      # Servidor de producción
bun run lint       # Verificación ESLint
```

## Licencia

Proyecto de uso libre.
