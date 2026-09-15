import { NextRequest, NextResponse } from 'next/server';
import { writeFile, readFile, mkdir, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { randomUUID } from 'node:crypto';
import ZAI from 'z-ai-web-dev-sdk';

export const runtime = 'nodejs';
export const maxDuration = 120;

interface ExtractRequest {
  pdfBase64: string;
  tableTitle: string;
  terms: string[];
  contextHint?: string;
}

interface ExtractedRow {
  termino: string;
  valor: string;
  unidad?: string;
  pagina?: number;
  confidencialidad?: 'alta' | 'media' | 'baja';
  notas?: string;
}

/**
 * Extrae texto del PDF usando pdftotext (vía pdf-text-extract).
 * Devuelve texto por páginas y metadata básica.
 */
async function extractPdfContent(pdfBuffer: Buffer) {
  // Escribir a archivo temporal porque pdftotext opera con archivos
  const tmpDir = join(tmpdir(), 'pdf-extractor-' + randomUUID());
  await mkdir(tmpDir, { recursive: true });
  const pdfPath = join(tmpDir, 'input.pdf');

  try {
    await writeFile(pdfPath, pdfBuffer);

    // Usar pdftotext directamente para máximo control
    const { execFile } = await import('node:child_process');
    const { promisify } = await import('node:util');
    const execFileAsync = promisify(execFile);

    let textContent = '';
    let totalPages = 0;

    // Primera pasada: obtener texto completo separado por form feeds (\f)
    try {
      const { stdout } = await execFileAsync('pdftotext', [
        '-layout', // Preservar layout de tablas
        '-enc', 'UTF-8',
        pdfPath,
        '-',
      ]);
      textContent = stdout;
      // Contar páginas por el caracter de form feed
      totalPages = stdout.split('\f').filter((p) => p.trim().length > 0).length;
    } catch (err) {
      console.error('[extract] pdftotext falló:', err);
      throw new Error(
        'No se pudo extraer texto del PDF con pdftotext. ' +
          (err instanceof Error ? err.message : '')
      );
    }

    // Obtener metadata con pdfinfo si está disponible
    let documentInfo: Record<string, string> | null = null;
    try {
      const { stdout } = await execFileAsync('pdfinfo', [pdfPath]);
      const info: Record<string, string> = {};
      stdout.split('\n').forEach((line) => {
        const idx = line.indexOf(':');
        if (idx > 0) {
          const key = line.substring(0, idx).trim();
          const value = line.substring(idx + 1).trim();
          info[key] = value;
        }
      });
      documentInfo = info;
    } catch {
      // pdfinfo no es crítico
    }

    // Formatear contenido por páginas para mejor contexto del LLM
    const pages = textContent.split('\f').filter((p) => p.trim().length > 0);
    const formattedPages = pages
      .map((page, i) => `\n=== PÁGINA ${i + 1} ===\n${page.trim()}`)
      .join('\n');

    // Truncar para no exceder el contexto del modelo
    const MAX_CHARS = 60000;
    const truncated =
      formattedPages.length > MAX_CHARS
        ? formattedPages.substring(0, MAX_CHARS) +
          '\n\n[... CONTENIDO TRUNCADO POR LONGITUD ...]'
        : formattedPages;

    return {
      content: truncated,
      totalPages,
      documentInfo,
      textLength: textContent.length,
    };
  } finally {
    // Limpieza del directorio temporal
    try {
      await rm(tmpDir, { recursive: true, force: true });
    } catch {
      // noop
    }
  }
}

/**
 * Construye el prompt estructurado para el LLM.
 */
function buildPrompt(
  tableTitle: string,
  terms: string[],
  pdfContent: string,
  contextHint?: string
): string {
  const termsList = terms.map((t, i) => `${i + 1}. ${t}`).join('\n');

  return `Eres un asistente experto en análisis técnico de documentos PDF. Tu tarea es extraer valores numéricos y datos asociados a términos clave desde el contenido de un PDF, específicamente desde la sección o tabla identificada por su título.

## CONTEXTO DEL DOCUMENTO
El siguiente contenido fue extraído automáticamente de un PDF (texto + estructura de tablas preservada con layout):

<pdf_content>
${pdfContent}
</pdf_content>

## INSTRUCCIONES
1. Ubica la sección/tabla cuyo título coincide (parcial o totalmente) con: "${tableTitle}".
   ${contextHint ? `2. Pista adicional del usuario: "${contextHint}"` : '2. No hay pista adicional del usuario.'}
3. Para CADA uno de los siguientes términos, busca su valor asociado en la tabla/sección identificada:
${termsList}

4. Para cada término, devuelve:
   - "valor": el valor numérico o textual encontrado (sin unidades, solo el número o dato principal). Si la tabla tiene varias columnas (ej: para diferentes mezclas o muestras), incluye todos los valores separados por " | " indicando a qué columna pertenecen.
   - "unidad": la unidad de medida si existe (ej: "MPa", "kg/m³", "mm", "°C", "%"). Vacío si no aplica.
   - "pagina": número de página donde se encontró (entero o null si no se sabe).
   - "confidencialidad": "alta" si el valor es exacto y claro, "media" si es una inferencia, "baja" si es una estimación o no se encontró con seguridad.
   - "notas": breve observación sobre cómo se obtuvo el valor o si hubo ambigüedad.

5. Si un término NO se encuentra en el documento, devuélvelo con valor: "NO_ENCONTRADO", unidad: "", confidencialidad: "baja", y una nota explicando que no se localizó.

## FORMATO DE RESPUESTA (ESTRICTO)
Devuelve ÚNICAMENTE un objeto JSON válido con esta estructura exacta, sin texto adicional, sin markdown, sin explicaciones:

{
  "resultados": [
    {
      "termino": "nombre exacto del término",
      "valor": "valor extraído",
      "unidad": "unidad si existe",
      "pagina": 12,
      "confidencialidad": "alta",
      "notas": "breve explicación"
    }
  ],
  "tabla_encontrada": true,
  "titulo_tabla_detectado": "título real encontrado en el PDF",
  "resumen": "breve resumen de 1-2 oraciones sobre la extracción"
}

IMPORTANTE: La respuesta debe ser JSON puro, sin bloques de código markdown, sin texto antes o después.`;
}

/**
 * Llama al modelo de IA con el prompt estructurado.
 */
async function callLLM(prompt: string): Promise<string> {
  const zai = await ZAI.create();

  const completion = await zai.chat.completions.create({
    messages: [
      {
        role: 'assistant',
        content:
          'Eres un asistente experto en extracción estructurada de datos de documentos técnicos. Respondes SIEMPRE en JSON válido, sin texto adicional.',
      },
      {
        role: 'user',
        content: prompt,
      },
    ],
    thinking: { type: 'disabled' },
    temperature: 0.1,
    max_tokens: 4096,
  });

  return completion.choices[0]?.message?.content || '';
}

/**
 * Limpia la respuesta del LLM para extraer JSON puro.
 */
function extractJsonFromResponse(raw: string): string {
  let cleaned = raw.trim();

  // Remover bloques markdown ```json ... ```
  const mdMatch = cleaned.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (mdMatch) {
    cleaned = mdMatch[1].trim();
  }

  // Buscar el primer { y el último }
  const firstBrace = cleaned.indexOf('{');
  const lastBrace = cleaned.lastIndexOf('}');
  if (firstBrace !== -1 && lastBrace !== -1 && lastBrace > firstBrace) {
    cleaned = cleaned.substring(firstBrace, lastBrace + 1);
  }

  return cleaned;
}

export async function POST(request: NextRequest) {
  try {
    const body = (await request.json()) as ExtractRequest;

    if (!body.pdfBase64 || !body.tableTitle || !body.terms || !Array.isArray(body.terms)) {
      return NextResponse.json(
        {
          error:
            'Faltan parámetros requeridos. Se necesita: pdfBase64, tableTitle, terms (array).',
        },
        { status: 400 }
      );
    }

    if (body.terms.length === 0) {
      return NextResponse.json(
        { error: 'Debe proporcionar al menos un término a catalogar.' },
        { status: 400 }
      );
    }

    // Decodificar PDF
    let pdfBuffer: Buffer;
    try {
      const base64Data = body.pdfBase64.includes(',')
        ? body.pdfBase64.split(',')[1]
        : body.pdfBase64;
      pdfBuffer = Buffer.from(base64Data, 'base64');
    } catch (err) {
      return NextResponse.json(
        { error: 'El PDF enviado no es un Base64 válido.' },
        { status: 400 }
      );
    }

    if (pdfBuffer.length < 1000) {
      return NextResponse.json(
        { error: 'El PDF parece estar vacío o corrupto (menos de 1 KB).' },
        { status: 400 }
      );
    }

    // Extraer contenido del PDF
    const pdfContent = await extractPdfContent(pdfBuffer);

    if (pdfContent.content.trim().length < 50) {
      return NextResponse.json(
        {
          error:
            'No se pudo extraer contenido del PDF. Puede ser un PDF escaneado sin capa de texto.',
          extractedLength: pdfContent.textLength,
        },
        { status: 422 }
      );
    }

    // Construir prompt y llamar al LLM
    const prompt = buildPrompt(
      body.tableTitle,
      body.terms,
      pdfContent.content,
      body.contextHint
    );

    const llmResponse = await callLLM(prompt);

    // Parsear respuesta JSON
    const jsonStr = extractJsonFromResponse(llmResponse);
    let parsed: {
      resultados?: ExtractedRow[];
      tabla_encontrada?: boolean;
      titulo_tabla_detectado?: string;
      resumen?: string;
    };

    try {
      parsed = JSON.parse(jsonStr);
    } catch (err) {
      console.error('[extract] Error parseando JSON del LLM:', err);
      console.error('[extract] Respuesta cruda:', llmResponse.substring(0, 500));
      return NextResponse.json(
        {
          error: 'El modelo no devolvió un JSON válido.',
          rawResponse: llmResponse.substring(0, 1000),
        },
        { status: 502 }
      );
    }

    if (!parsed.resultados || !Array.isArray(parsed.resultados)) {
      return NextResponse.json(
        {
          error: 'La respuesta del modelo no contiene el campo "resultados" esperado.',
          rawResponse: llmResponse.substring(0, 1000),
        },
        { status: 502 }
      );
    }

    return NextResponse.json({
      success: true,
      resultados: parsed.resultados,
      tabla_encontrada: parsed.tabla_encontrada ?? false,
      titulo_tabla_detectado: parsed.titulo_tabla_detectado || body.tableTitle,
      resumen: parsed.resumen || '',
      metadata: {
        totalPages: pdfContent.totalPages,
        documentInfo: pdfContent.documentInfo,
        textLength: pdfContent.textLength,
        termsCount: body.terms.length,
      },
    });
  } catch (error) {
    console.error('[extract] Error no controlado:', error);
    const message = error instanceof Error ? error.message : 'Error desconocido';
    return NextResponse.json(
      { error: `Error interno del servidor: ${message}` },
      { status: 500 }
    );
  }
}
