'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { FileUploader } from '@/components/extractor/file-uploader';
import { ResultsTable, type ExtractionResult } from '@/components/extractor/results-table';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Progress } from '@/components/ui/progress';
import { AlertCircle, CheckCircle2, FileSearch, Loader2, RefreshCw, ShieldCheck, Square, WifiOff } from 'lucide-react';
import { toast } from 'sonner';

type Status = 'idle' | 'processing' | 'done' | 'error' | 'cancelled';
type ApiJob = { job_id: string; status: string; error?: string | null; result?: ExtractionResult | null; download_url?: string };
type Health = { status: string; capabilities?: { text?: boolean; ocr?: boolean } };

async function readResponse(response: Response): Promise<ApiJob> {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Error ${response.status}`);
  return data as ApiJob;
}

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [backendOnline, setBackendOnline] = useState<boolean | null>(null);
  const [status, setStatus] = useState<Status>('idle');
  const [phase, setPhase] = useState('Listo para procesar');
  const [progress, setProgress] = useState(0);
  const [jobId, setJobId] = useState<string | null>(null);
  const [result, setResult] = useState<ExtractionResult | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const polling = useRef(false);

  const checkBackend = useCallback(async () => {
    try {
      const response = await fetch('/api/local/health', { cache: 'no-store' });
      const health = response.ok ? await response.json() as Health : null;
      const ready = Boolean(response.ok && health?.capabilities?.text);
      setBackendOnline(ready);
      return ready;
    } catch { setBackendOnline(false); return false; }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { void checkBackend(); }, 0);
    return () => window.clearTimeout(timer);
  }, [checkBackend]);

  const poll = useCallback(async (id: string) => {
    polling.current = true;
    try {
      while (polling.current) {
        const response = await fetch(`/api/local/jobs/${id}`, { cache: 'no-store' });
        const job = await readResponse(response);
        if (job.status === 'queued') { setPhase('En cola · esperando turno'); setProgress(20); }
        else if (job.status === 'running' || job.status === 'cancel_requested') { setPhase(job.status === 'cancel_requested' ? 'Cancelando y cerrando procesos…' : 'Extrayendo texto y buscando el cuadro de cubicación…'); setProgress(65); }
        if (job.status === 'completed' && job.result) {
          setResult(job.result); setProgress(100); setPhase('Proceso completado'); setStatus('done');
          toast.success('PDF procesado localmente'); return;
        }
        if (job.status === 'failed') throw new Error(job.error || 'El backend no pudo procesar el PDF');
        if (job.status === 'cancelled') { setStatus('cancelled'); setPhase('Trabajo cancelado'); return; }
        await new Promise((resolve) => window.setTimeout(resolve, 700));
      }
    } catch (error) {
      if (polling.current) { setErrorMessage(error instanceof Error ? error.message : 'Error desconocido'); setStatus('error'); setPhase('El proceso terminó con error'); }
    } finally { polling.current = false; }
  }, []);

  const process = async () => {
    if (!file) { toast.error('Seleccione un PDF'); return; }
    if (!(await checkBackend())) { toast.error('Inicie el backend local offline'); return; }
    setStatus('processing'); setProgress(10); setPhase('Subiendo al espacio local controlado…'); setErrorMessage(null); setResult(null);
    const form = new FormData(); form.append('pdf', file, file.name); form.append('ocr', 'false');
    try {
      const response = await fetch('/api/local/jobs', { method: 'POST', body: form });
      const job = await readResponse(response);
      setJobId(job.job_id);
      void poll(job.job_id);
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'No se pudo crear el trabajo'); setStatus('error'); setPhase('No se pudo iniciar');
    }
  };

  const cancel = async () => {
    if (!jobId) return;
    polling.current = true;
    try { await fetch(`/api/local/jobs/${jobId}`, { method: 'DELETE' }); setPhase('Cancelando y cerrando procesos…'); }
    catch { toast.error('No se pudo solicitar la cancelación'); }
  };

  const reset = () => { polling.current = false; setFile(null); setJobId(null); setResult(null); setErrorMessage(null); setStatus('idle'); setProgress(0); setPhase('Listo para procesar'); };
  const downloadUrl = result && jobId ? `/api/local/jobs/${jobId}/excel` : undefined;

  return <div className="min-h-screen bg-gradient-to-br from-slate-50 via-white to-slate-100">
    <header className="border-b bg-white/90"><div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 sm:px-6">
      <div className="flex items-center gap-3"><div className="flex size-10 items-center justify-center rounded-lg bg-emerald-700 text-white"><FileSearch className="size-5" /></div><div><h1 className="text-lg font-bold">Cubicador IA Local</h1><p className="text-xs text-muted-foreground">PDF → cuadro de cubicación → Excel</p></div></div>
      <div className="flex items-center gap-2"><Badge variant="outline" className="gap-1 border-emerald-300 text-emerald-700"><ShieldCheck className="size-3" />Offline</Badge><Badge variant={backendOnline ? 'default' : 'secondary'}>{backendOnline ? 'Backend conectado' : 'Backend desconectado'}</Badge></div>
    </div></header>
    <main className="mx-auto max-w-6xl space-y-6 px-4 py-6 sm:px-6">
      {backendOnline === false && <div className="flex items-start gap-3 rounded-xl border border-amber-300 bg-amber-50 p-4 text-sm text-amber-950"><WifiOff className="mt-0.5 size-5 shrink-0" /><div><p className="font-semibold">Motor local no disponible</p><p className="mt-1 text-xs">Inicie el servicio Python con su paquete Poppler verificado. El navegador no enviará archivos a Internet.</p></div></div>}
      <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950"><p className="font-semibold">Flujo controlado</p><p className="mt-1 text-xs">Se busca únicamente “Tabla de cubicación” o “Cuadro de cubicación”. El resultado conserva evidencia, advertencias y hash del PDF.</p></div>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,420px)_1fr]">
        <div className="space-y-4"><FileUploader onFileSelected={(selected) => { setFile(selected); setResult(null); setStatus('idle'); setErrorMessage(null); }} disabled={status === 'processing'} />
          <Card><CardHeader><CardTitle className="text-base">Procesamiento</CardTitle><CardDescription>OCR no se activa hasta que el bundle local firmado esté disponible.</CardDescription></CardHeader><CardContent className="space-y-3"><Button onClick={process} disabled={!file || status === 'processing' || backendOnline === false} className="w-full gap-2 bg-emerald-700 hover:bg-emerald-800">{status === 'processing' ? <><Loader2 className="size-4 animate-spin" />Procesando…</> : <><FileSearch className="size-4" />Procesar PDF</>}</Button>{status === 'processing' && <><Progress value={progress} className="h-2" /><div className="flex items-center justify-between gap-2 text-xs text-muted-foreground"><span>{phase}</span><Button size="sm" variant="outline" onClick={cancel} className="h-7 gap-1"><Square className="size-3" />Cancelar</Button></div></>}{status === 'error' && <div className="flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive"><AlertCircle className="mt-0.5 size-4 shrink-0" /><div><p className="font-medium">{phase}</p><p>{errorMessage}</p></div></div>}{status === 'done' && <div className="flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 p-3 text-xs text-emerald-800"><CheckCircle2 className="size-4" />Resultado listo para revisar y descargar.</div>}{status === 'cancelled' && <div className="rounded-md border border-slate-300 bg-slate-50 p-3 text-xs text-slate-700">El resultado fue descartado.</div>}<Button variant="ghost" size="sm" onClick={reset} className="w-full gap-1"><RefreshCw className="size-3.5" />Nuevo PDF</Button></CardContent></Card>
        </div>
        <div>{result ? <ResultsTable result={result} downloadUrl={downloadUrl} /> : <Card className="flex min-h-[430px] items-center justify-center"><CardContent className="text-center"><FileSearch className="mx-auto size-10 text-muted-foreground" /><p className="mt-3 text-sm font-medium">Resultados aparecerán aquí</p><p className="mt-1 max-w-sm text-xs text-muted-foreground">Seleccione un PDF para comenzar. Los casos sin texto seleccionable o con evidencia ambigua quedan pendientes de revisión.</p></CardContent></Card>}</div>
      </div>
      <footer className="border-t pt-5 text-center text-xs text-muted-foreground">Procesamiento local · cola de un trabajo · límites de archivos, tiempo y procesos · sin servicios externos</footer>
    </main>
  </div>;
}
