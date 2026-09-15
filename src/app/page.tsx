'use client';

import { useState, useCallback } from 'react';
import { FileUploader } from '@/components/extractor/file-uploader';
import { ParametersForm } from '@/components/extractor/parameters-form';
import { ResultsTable, type ExtractionResult } from '@/components/extractor/results-table';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { Badge } from '@/components/ui/badge';
import {
  FileSearch,
  Loader2,
  Sparkles,
  AlertCircle,
  CheckCircle2,
  ArrowRight,
  RefreshCw,
} from 'lucide-react';
import { toast } from 'sonner';

type Status = 'idle' | 'uploading' | 'processing' | 'done' | 'error';

export default function Home() {
  const [pdfBase64, setPdfBase64] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [tableTitle, setTableTitle] = useState('');
  const [termsText, setTermsText] = useState('');
  const [contextHint, setContextHint] = useState('');
  const [status, setStatus] = useState<Status>('idle');
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<ExtractionResult | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleFileSelected = useCallback(
    (file: File | null, base64: string | null) => {
      setPdfBase64(base64);
      setFileName(file?.name ?? null);
      setResult(null);
      setStatus('idle');
      setErrorMsg(null);
    },
    []
  );

  const canSubmit = Boolean(
    pdfBase64 && tableTitle.trim() && termsText.trim() && status !== 'processing'
  );

  const parseTerms = (text: string): string[] => {
    return text
      .split(/[\n,]/)
      .map((t) => t.trim())
      .filter((t) => t.length > 0);
  };

  const handleExtract = async () => {
    if (!pdfBase64 || !tableTitle.trim() || !termsText.trim()) {
      toast.error('Complete todos los campos requeridos');
      return;
    }

    const terms = parseTerms(termsText);
    if (terms.length === 0) {
      toast.error('Agregue al menos un término');
      return;
    }

    setStatus('processing');
    setProgress(10);
    setErrorMsg(null);
    setResult(null);

    // Animación de progreso suave
    const progressTimer = setInterval(() => {
      setProgress((p) => {
        if (p >= 90) return p;
        return p + Math.random() * 8;
      });
    }, 800);

    try {
      const response = await fetch('/api/extract', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pdfBase64,
          tableTitle: tableTitle.trim(),
          terms,
          contextHint: contextHint.trim() || undefined,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || `Error ${response.status}`);
      }

      setProgress(100);
      setResult(data as ExtractionResult);
      setStatus('done');
      toast.success(`Extracción completada: ${data.resultados?.length ?? 0} términos procesados`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Error desconocido';
      setErrorMsg(msg);
      setStatus('error');
      toast.error(`Error: ${msg}`);
    } finally {
      clearInterval(progressTimer);
      setTimeout(() => setProgress(0), 1200);
    }
  };

  const handleReset = () => {
    setResult(null);
    setStatus('idle');
    setErrorMsg(null);
    setProgress(0);
  };

  const handleNewExtraction = () => {
    setPdfBase64(null);
    setFileName(null);
    setTableTitle('');
    setTermsText('');
    setContextHint('');
    setResult(null);
    setStatus('idle');
    setErrorMsg(null);
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-slate-100 dark:from-slate-950 dark:via-slate-900 dark:to-slate-950">
      {/* Header */}
      <header className="border-b bg-white/80 backdrop-blur-sm dark:bg-slate-900/80">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4 sm:px-6 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-gradient-to-br from-emerald-600 to-teal-700 text-white shadow-sm">
              <FileSearch className="size-5" />
            </div>
            <div>
              <h1 className="text-lg font-bold tracking-tight text-slate-900 dark:text-white">
                Extractor PDF + IA
              </h1>
              <p className="text-xs text-slate-500 dark:text-slate-400">
                Catalogación automática de tablas técnicas
              </p>
            </div>
          </div>
          <div className="hidden items-center gap-2 sm:flex">
            <Badge variant="outline" className="gap-1 border-emerald-300 text-emerald-700 dark:text-emerald-400">
              <Sparkles className="size-3" />
              IA Local
            </Badge>
            <Button variant="ghost" size="sm" onClick={handleNewExtraction} className="gap-1.5">
              <RefreshCw className="size-3.5" />
              Nueva extracción
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        {/* Intro banner */}
        {status === 'idle' && !result && (
          <div className="mb-6 rounded-xl border border-emerald-200 bg-gradient-to-r from-emerald-50 to-teal-50 p-4 dark:border-emerald-900 dark:from-emerald-950/40 dark:to-teal-950/40">
            <div className="flex items-start gap-3">
              <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-emerald-600 text-white">
                <Sparkles className="size-4" />
              </div>
              <div className="flex-1">
                <h2 className="text-sm font-semibold text-emerald-900 dark:text-emerald-100">
                  Cómo funciona
                </h2>
                <ol className="mt-1 grid gap-1 text-xs text-emerald-800 dark:text-emerald-200 sm:grid-cols-3">
                  <li>
                    <strong>1.</strong> Cargue un PDF y defina el título de la tabla a buscar.
                  </li>
                  <li>
                    <strong>2.</strong> Liste los términos cuyos valores desea extraer.
                  </li>
                  <li>
                    <strong>3.</strong> La IA analiza el documento y cataloga cada valor en una tabla editable y exportable.
                  </li>
                </ol>
              </div>
            </div>
          </div>
        )}

        <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
          {/* Columna izquierda: inputs */}
          <div className="flex flex-col gap-4">
            <FileUploader onFileSelected={handleFileSelected} disabled={status === 'processing'} />

            <ParametersForm
              tableTitle={tableTitle}
              onTableTitleChange={setTableTitle}
              termsText={termsText}
              onTermsTextChange={setTermsText}
              contextHint={contextHint}
              onContextHintChange={setContextHint}
              disabled={status === 'processing'}
            />

            <Card>
              <CardContent className="pt-6">
                <Button
                  onClick={handleExtract}
                  disabled={!canSubmit}
                  size="lg"
                  className="w-full gap-2 bg-gradient-to-r from-emerald-600 to-teal-700 hover:from-emerald-700 hover:to-teal-800"
                >
                  {status === 'processing' ? (
                    <>
                      <Loader2 className="size-4 animate-spin" />
                      Procesando...
                    </>
                  ) : (
                    <>
                      <FileSearch className="size-4" />
                      Extraer y Catalogar
                      <ArrowRight className="size-4" />
                    </>
                  )}
                </Button>

                {status === 'processing' && (
                  <div className="mt-3 space-y-1.5">
                    <Progress value={progress} className="h-1.5" />
                    <p className="text-center text-xs text-muted-foreground">
                      Analizando PDF con IA · {Math.round(progress)}%
                    </p>
                  </div>
                )}

                {status === 'error' && errorMsg && (
                  <div className="mt-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                    <AlertCircle className="size-3.5 mt-0.5 shrink-0" />
                    <div className="flex-1">
                      <p className="font-medium">Error en la extracción</p>
                      <p className="mt-0.5 opacity-90">{errorMsg}</p>
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={handleReset}
                        className="mt-2 h-7 gap-1 px-2 text-xs"
                      >
                        <RefreshCw className="size-3" />
                        Reintentar
                      </Button>
                    </div>
                  </div>
                )}

                {status === 'done' && result && (
                  <div className="mt-3 flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">
                    <CheckCircle2 className="size-3.5 shrink-0" />
                    <span>
                      Extracción completada · {result.resultados.length} términos ·{' '}
                      {result.tabla_encontrada ? 'Tabla localizada' : 'Búsqueda parcial'}
                    </span>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          {/* Columna derecha: resultados */}
          <div className="flex flex-col gap-4">
            {result ? (
              <ResultsTable key={JSON.stringify(result.resultados)} result={result} />
            ) : (
              <Card className="flex min-h-[400px] flex-1 items-center justify-center">
                <CardContent className="flex flex-col items-center gap-3 py-12 text-center">
                  <div className="flex size-16 items-center justify-center rounded-full bg-muted">
                    <FileSearch className="size-7 text-muted-foreground" />
                  </div>
                  <div className="space-y-1">
                    <p className="text-sm font-medium">Resultados aparecerán aquí</p>
                    <p className="max-w-xs text-xs text-muted-foreground">
                      Complete el formulario y ejecute la extracción para ver la tabla catalogada con valores, unidades y nivel de confianza.
                    </p>
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        </div>

        {/* Footer info */}
        <footer className="mt-10 border-t pt-6 text-center">
          <p className="text-xs text-muted-foreground">
            Sistema de extracción local · PDF → IA → Tabla exportable · Los archivos se procesan
            en sesión y no se almacenan permanentemente.
          </p>
        </footer>
      </main>
    </div>
  );
}
