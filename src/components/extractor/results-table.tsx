'use client';

import { useState } from 'react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import {
  Copy,
  Download,
  Pencil,
  Check,
  X,
  FileSpreadsheet,
  ClipboardCheck,
  TableProperties,
  Info,
} from 'lucide-react';
import { toast } from 'sonner';

export interface ResultRow {
  termino: string;
  valor: string;
  unidad: string;
  pagina: number | null;
  confidencialidad: 'alta' | 'media' | 'baja';
  notas: string;
}

export interface ExtractionResult {
  success: boolean;
  resultados: ResultRow[];
  tabla_encontrada: boolean;
  titulo_tabla_detectado: string;
  resumen: string;
  metadata: {
    totalPages: number;
    documentInfo: {
      title?: string;
      author?: string;
      creator?: string;
      producer?: string;
      totalPages?: number;
    } | null;
    textLength: number;
    tableLength: number;
    termsCount: number;
  };
}

interface ResultsTableProps {
  result: ExtractionResult | null;
}

const confidencialidadConfig = {
  alta: { label: 'Alta', variant: 'default' as const, className: 'bg-emerald-600 hover:bg-emerald-600' },
  media: { label: 'Media', variant: 'secondary' as const, className: 'bg-amber-500 hover:bg-amber-500 text-white' },
  baja: { label: 'Baja', variant: 'outline' as const, className: 'border-red-300 text-red-700' },
};

export function ResultsTable({ result }: ResultsTableProps) {
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editBuffer, setEditBuffer] = useState<ResultRow | null>(null);
  // Inicializar desde props - el componente se remonta cuando cambia `result`
  // gracias al `key` prop que asigna el padre.
  const [rows, setRows] = useState<ResultRow[]>(() => result?.resultados ?? []);

  if (!result) return null;

  const startEdit = (index: number) => {
    setEditingIndex(index);
    setEditBuffer({ ...rows[index] });
  };

  const cancelEdit = () => {
    setEditingIndex(null);
    setEditBuffer(null);
  };

  const saveEdit = () => {
    if (editingIndex !== null && editBuffer) {
      const newRows = [...rows];
      newRows[editingIndex] = editBuffer;
      setRows(newRows);
      toast.success('Fila actualizada');
    }
    cancelEdit();
  };

  const copyToClipboard = async () => {
    const text = rows
      .map((r) => `${r.termino}\t${r.valor}\t${r.unidad}\t${r.pagina ?? ''}\t${r.confidencialidad}\t${r.notas}`)
      .join('\n');
    const header = 'Término\tValor\tUnidad\tPágina\tConfidencialidad\tNotas';
    try {
      await navigator.clipboard.writeText(`${header}\n${text}`);
      toast.success('Tabla copiada al portapapeles');
    } catch {
      toast.error('No se pudo copiar al portapapeles');
    }
  };

  const exportToCSV = () => {
    const escape = (v: string) => {
      const s = String(v ?? '');
      if (s.includes('"') || s.includes(',') || s.includes('\n')) {
        return `"${s.replace(/"/g, '""')}"`;
      }
      return s;
    };
    const header = ['Término', 'Valor', 'Unidad', 'Página', 'Confidencialidad', 'Notas'].join(',');
    const body = rows
      .map((r) =>
        [
          escape(r.termino),
          escape(r.valor),
          escape(r.unidad),
          r.pagina ?? '',
          r.confidencialidad,
          escape(r.notas),
        ].join(',')
      )
      .join('\n');
    const csv = `\uFEFF${header}\n${body}`;
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `extraccion-pdf-${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    toast.success('Archivo CSV descargado');
  };

  const copyAsJson = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(rows, null, 2));
      toast.success('JSON copiado al portapapeles');
    } catch {
      toast.error('No se pudo copiar el JSON');
    }
  };

  const encontrados = rows.filter((r) => r.valor !== 'NO_ENCONTRADO').length;
  const noEncontrados = rows.length - encontrados;

  return (
    <Card className="w-full">
      <CardHeader>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <TableProperties className="size-4 text-primary" />
              Tabla de Resultados
            </CardTitle>
            <CardDescription className="mt-1">
              {result.titulo_tabla_detectado && (
                <span className="block text-xs">
                  Tabla detectada: <strong>{result.titulo_tabla_detectado}</strong>
                </span>
              )}
            </CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={result.tabla_encontrada ? 'default' : 'destructive'} className="text-[10px]">
              {result.tabla_encontrada ? 'Tabla encontrada' : 'Sin coincidencia exacta'}
            </Badge>
            <Badge variant="secondary" className="text-[10px]">
              {encontrados} encontrados
            </Badge>
            {noEncontrados > 0 && (
              <Badge variant="outline" className="text-[10px] border-red-300 text-red-700">
                {noEncontrados} sin datos
              </Badge>
            )}
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Resumen + metadata */}
        {result.resumen && (
          <div className="flex items-start gap-2 rounded-md border bg-muted/30 px-3 py-2 text-xs">
            <Info className="size-3.5 mt-0.5 shrink-0 text-muted-foreground" />
            <div className="flex-1">
              <p>{result.resumen}</p>
              {result.metadata.totalPages > 0 && (
                <p className="mt-1 text-muted-foreground">
                  PDF: {result.metadata.totalPages} páginas ·{' '}
                  {result.metadata.documentInfo?.title
                    ? `"${result.metadata.documentInfo.title}"`
                    : 'sin título en metadata'}
                </p>
              )}
            </div>
          </div>
        )}

        {/* Botones de exportación */}
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="default" onClick={exportToCSV} className="gap-1.5">
            <FileSpreadsheet className="size-3.5" />
            Exportar CSV
          </Button>
          <Button size="sm" variant="outline" onClick={copyToClipboard} className="gap-1.5">
            <Copy className="size-3.5" />
            Copiar tabla
          </Button>
          <Button size="sm" variant="outline" onClick={copyAsJson} className="gap-1.5">
            <ClipboardCheck className="size-3.5" />
            Copiar JSON
          </Button>
        </div>

        {/* Tabla editable */}
        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40">
                <TableHead className="w-[26%]">Término</TableHead>
                <TableHead className="w-[16%]">Valor</TableHead>
                <TableHead className="w-[10%]">Unidad</TableHead>
                <TableHead className="w-[8%] text-center">Pág.</TableHead>
                <TableHead className="w-[12%] text-center">Conf.</TableHead>
                <TableHead className="w-[20%]">Notas</TableHead>
                <TableHead className="w-[8%] text-center">Acción</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row, idx) => {
                const isEditing = editingIndex === idx;
                const conf = confidencialidadConfig[row.confidencialidad] || confidencialidadConfig.baja;
                const isNotFound = row.valor === 'NO_ENCONTRADO';

                if (isEditing && editBuffer) {
                  return (
                    <TableRow key={idx} className="bg-primary/5">
                      <TableCell>
                        <Input
                          value={editBuffer.termino}
                          onChange={(e) => setEditBuffer({ ...editBuffer, termino: e.target.value })}
                          className="h-8 text-xs"
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          value={editBuffer.valor}
                          onChange={(e) => setEditBuffer({ ...editBuffer, valor: e.target.value })}
                          className="h-8 text-xs"
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          value={editBuffer.unidad}
                          onChange={(e) => setEditBuffer({ ...editBuffer, unidad: e.target.value })}
                          className="h-8 text-xs"
                        />
                      </TableCell>
                      <TableCell>
                        <Input
                          type="number"
                          value={editBuffer.pagina ?? ''}
                          onChange={(e) =>
                            setEditBuffer({
                              ...editBuffer,
                              pagina: e.target.value ? Number(e.target.value) : null,
                            })
                          }
                          className="h-8 text-xs text-center"
                        />
                      </TableCell>
                      <TableCell>
                        <select
                          value={editBuffer.confidencialidad}
                          onChange={(e) =>
                            setEditBuffer({
                              ...editBuffer,
                              confidencialidad: e.target.value as ResultRow['confidencialidad'],
                            })
                          }
                          className="h-8 w-full rounded-md border bg-background px-1 text-xs"
                        >
                          <option value="alta">Alta</option>
                          <option value="media">Media</option>
                          <option value="baja">Baja</option>
                        </select>
                      </TableCell>
                      <TableCell>
                        <Textarea
                          value={editBuffer.notas}
                          onChange={(e) => setEditBuffer({ ...editBuffer, notas: e.target.value })}
                          rows={1}
                          className="min-h-8 text-xs"
                        />
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center justify-center gap-1">
                          <Button size="icon" variant="ghost" onClick={saveEdit} className="size-7">
                            <Check className="size-3.5 text-emerald-600" />
                          </Button>
                          <Button size="icon" variant="ghost" onClick={cancelEdit} className="size-7">
                            <X className="size-3.5 text-destructive" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                }

                return (
                  <TableRow key={idx} className={isNotFound ? 'opacity-70' : ''}>
                    <TableCell className="text-xs font-medium">{row.termino}</TableCell>
                    <TableCell className="text-xs">
                      {isNotFound ? (
                        <span className="text-destructive italic">No encontrado</span>
                      ) : (
                        <span className="font-mono font-semibold">{row.valor}</span>
                      )}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{row.unidad || '—'}</TableCell>
                    <TableCell className="text-center text-xs text-muted-foreground">
                      {row.pagina ?? '—'}
                    </TableCell>
                    <TableCell className="text-center">
                      <Badge variant={conf.variant} className={`text-[10px] ${conf.className}`}>
                        {conf.label}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {row.notas || '—'}
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-center">
                        <Button
                          size="icon"
                          variant="ghost"
                          onClick={() => startEdit(idx)}
                          className="size-7"
                          aria-label="Editar fila"
                        >
                          <Pencil className="size-3.5" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </CardContent>
    </Card>
  );
}
