'use client';

import { useMemo } from 'react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { ClipboardCheck, Download, Info, ShieldAlert, TableProperties } from 'lucide-react';
import { toast } from 'sonner';

export interface QuantityValue {
  column: string;
  original: string;
  numeric_value: number | null;
  parse_status: 'parsed' | 'ambiguous' | 'invalid';
  warning?: string | null;
  bbox?: [number, number, number, number] | null;
  confidence?: number | null;
}

export interface Evidence {
  page: number;
  line_start: number;
  line_end: number;
  text: string;
  bbox?: [number, number, number, number] | null;
  confidence?: number | null;
  engine?: string | null;
  model_version?: string | null;
}

export interface QuantityRow {
  item: string;
  descripcion: string;
  unidad: string;
  cells_original: string[];
  quantities: QuantityValue[];
  tipo_fila: 'detalle' | 'subtotal' | 'total' | 'nota';
  pagina: number;
  evidencia: Evidence;
}

export interface ExtractionResult {
  archivo: string;
  tabla_encontrada: boolean;
  titulo_tabla: string | null;
  columns: string[];
  filas: QuantityRow[];
  requiere_revision: boolean;
  candidatos: string[];
  advertencias: string[];
  sha256: string;
  page_count: number;
  extractor_version: string;
}

interface ResultsTableProps { result: ExtractionResult; downloadUrl?: string; }

export function ResultsTable({ result, downloadUrl }: ResultsTableProps) {
  const quantities = useMemo(() => result.filas.flatMap((row) => row.quantities.map((quantity) => ({ row, quantity }))), [result]);
  const pending = result.advertencias.length + quantities.filter(({ quantity }) => quantity.parse_status !== 'parsed').length;
  const copyJson = async () => {
    try { await navigator.clipboard.writeText(JSON.stringify(result, null, 2)); toast.success('Resultado copiado como JSON'); }
    catch { toast.error('No se pudo copiar el resultado'); }
  };
  return (
    <Card className="w-full">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div><CardTitle className="flex items-center gap-2 text-base"><TableProperties className="size-4 text-primary" />Cuadro de cubicación</CardTitle>
            <CardDescription className="mt-1">{result.archivo} · {result.page_count} páginas · {result.extractor_version}</CardDescription></div>
          <div className="flex flex-wrap gap-2"><Badge variant={result.tabla_encontrada ? 'default' : 'destructive'}>{result.tabla_encontrada ? 'Tabla encontrada' : 'Tabla no encontrada'}</Badge>
            {result.requiere_revision && <Badge variant="outline" className="border-amber-400 text-amber-700">Requiere revisión</Badge>}</div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {(result.advertencias.length > 0 || result.candidatos.length > 1) && <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-950"><div className="flex items-start gap-2"><ShieldAlert className="mt-0.5 size-4 shrink-0" /><div>{result.advertencias.map((warning) => <p key={warning}>{warning}</p>)}{result.candidatos.length > 0 && <p className="mt-1">Candidatos: {result.candidatos.join(' · ')}</p>}</div></div></div>}
        <div className="flex flex-wrap items-center gap-2">
          {downloadUrl && <Button size="sm" asChild className="gap-1.5"><a href={downloadUrl} download><Download className="size-3.5" />Descargar XLSX</a></Button>}
          <Button size="sm" variant="outline" onClick={copyJson} className="gap-1.5"><ClipboardCheck className="size-3.5" />Copiar JSON</Button>
          <span className="ml-auto text-xs text-muted-foreground">{quantities.length} cantidades · {pending} pendientes</span>
        </div>
        {result.filas.length > 0 ? <div className="overflow-x-auto rounded-md border"><Table><TableHeader><TableRow className="bg-muted/40"><TableHead>Ítem</TableHead><TableHead>Descripción</TableHead><TableHead>Unidad</TableHead><TableHead>Cantidades</TableHead><TableHead>Pág.</TableHead><TableHead>Evidencia</TableHead></TableRow></TableHeader><TableBody>{result.filas.map((row, index) => <TableRow key={`${row.item}-${index}`}>
          <TableCell className="text-xs font-medium">{row.item}</TableCell><TableCell className="min-w-44 text-xs">{row.descripcion}</TableCell><TableCell className="text-xs text-muted-foreground">{row.unidad}</TableCell>
          <TableCell className="text-xs">{row.quantities.map((quantity) => <div key={quantity.column}><span className={quantity.parse_status === 'parsed' ? 'font-mono font-semibold' : 'text-amber-700'}>{quantity.column}: {quantity.original}</span>{quantity.parse_status !== 'parsed' && <span className="ml-1 text-[10px]">({quantity.warning || 'revisar'})</span>}</div>)}</TableCell>
          <TableCell className="text-center text-xs text-muted-foreground">{row.pagina}</TableCell><TableCell className="max-w-48 text-xs text-muted-foreground" title={row.evidencia.text}>L{row.evidencia.line_start} · {row.evidencia.confidence != null ? `${Math.round(row.evidencia.confidence * 100)}%` : 'texto'}</TableCell>
        </TableRow>)}</TableBody></Table></div> : <div className="rounded-md border border-dashed px-4 py-10 text-center text-sm text-muted-foreground">No se generaron filas verificables.</div>}
        <div className="flex items-start gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs"><Info className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" /><p>Hash SHA-256: <span className="font-mono break-all">{result.sha256}</span>. Cada fila conserva página, línea y texto de evidencia.</p></div>
      </CardContent>
    </Card>
  );
}
