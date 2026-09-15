'use client';

import { useState, useCallback, useRef, DragEvent } from 'react';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { FileText, Upload, X, FileUp, AlertCircle } from 'lucide-react';
import { cn } from '@/lib/utils';

interface FileUploaderProps {
  onFileSelected: (file: File | null, base64: string | null) => void;
  disabled?: boolean;
}

export function FileUploader({ onFileSelected, disabled }: FileUploaderProps) {
  const [dragging, setDragging] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fileSize, setFileSize] = useState<string>('');
  const inputRef = useRef<HTMLInputElement>(null);

  const formatBytes = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  };

  const handleFile = useCallback(
    (file: File) => {
      setError(null);
      if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
        setError('El archivo debe ser un PDF vÃ¡lido.');
        return;
      }
      if (file.size > 25 * 1024 * 1024) {
        setError('El archivo excede el tamaÃ±o mÃ¡ximo de 25 MB.');
        return;
      }
      setSelectedFile(file);
      setFileSize(formatBytes(file.size));
      const reader = new FileReader();
      reader.onload = () => {
        const result = reader.result as string;
        onFileSelected(file, result);
      };
      reader.onerror = () => {
        setError('No se pudo leer el archivo. Intente nuevamente.');
      };
      reader.readAsDataURL(file);
    },
    [onFileSelected]
  );

  const handleDrop = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      setDragging(false);
      if (disabled) return;
      const files = e.dataTransfer.files;
      if (files && files.length > 0) {
        handleFile(files[0]);
      }
    },
    [handleFile, disabled]
  );

  const handleDragOver = useCallback(
    (e: DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      if (!disabled) setDragging(true);
    },
    [disabled]
  );

  const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
  }, []);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) handleFile(file);
    },
    [handleFile]
  );

  const handleRemove = useCallback(() => {
    setSelectedFile(null);
    setFileSize('');
    setError(null);
    onFileSelected(null, null);
    if (inputRef.current) inputRef.current.value = '';
  }, [onFileSelected]);

  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <FileUp className="size-4 text-primary" />
          Documento PDF
        </CardTitle>
        <CardDescription>
          Seleccione o arrastre el PDF desde el cual se extraerÃ¡n los datos.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {!selectedFile ? (
          <div
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => !disabled && inputRef.current?.click()}
            className={cn(
              'flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors',
              dragging
                ? 'border-primary bg-primary/5'
                : 'border-muted-foreground/30 hover:border-primary/50 hover:bg-muted/30',
              disabled && 'cursor-not-allowed opacity-60'
            )}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if ((e.key === 'Enter' || e.key === ' ') && !disabled) {
                e.preventDefault();
                inputRef.current?.click();
              }
            }}
          >
            <div className="flex size-12 items-center justify-center rounded-full bg-muted">
              <Upload className="size-5 text-muted-foreground" />
            </div>
            <div className="space-y-1">
              <p className="text-sm font-medium">
                Haga clic o arrastre un archivo PDF aquÃ­
              </p>
              <p className="text-xs text-muted-foreground">
                Formato PDF Â· MÃ¡ximo 25 MB
              </p>
            </div>
            <input
              ref={inputRef}
              type="file"
              accept="application/pdf,.pdf"
              onChange={handleInputChange}
              className="hidden"
              disabled={disabled}
            />
          </div>
        ) : (
          <div className="flex items-center justify-between rounded-lg border bg-muted/30 p-4">
            <div className="flex min-w-0 items-center gap-3">
              <div className="flex size-10 shrink-0 items-center justify-center rounded-md bg-primary/10">
                <FileText className="size-5 text-primary" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium" title={selectedFile.name}>
                  {selectedFile.name}
                </p>
                <div className="mt-1 flex items-center gap-2">
                  <Badge variant="secondary" className="text-[10px]">PDF</Badge>
                  <span className="text-xs text-muted-foreground">{fileSize}</span>
                </div>
              </div>
            </div>
            <Button
       #~ùÓ«h‘éì¶»§q«^uÉ½Õ¹É½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹Ôé‘…Ñ„µmÍÑ…Ñ”õ½Á•¹té…¹¥µ…Ñ”µ¥¸É½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹Ôé‘…Ñ„µmÍÑ…Ñ”õ±½Í•‘té…¹¥µ…Ñ”µ½ÕÐÉ½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹Ôé‘…Ñ„µmÍÑ…Ñ”õ±½Í•‘téé½½´µ½ÕÐ´äÔÉ½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹Ôé‘…Ñ„µmÍÑ…Ñ”õ½Á•¹téé½½´µ¥¸´äÔÉ½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹Ôé‘…Ñ„µmÍÑ…Ñ”õ½Á•¹té™…‘”µ¥¸´ÀÉ½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹Ôé‘…Ñ„µmÍÑ…Ñ”õ±½Í•‘té™…‘”µ½ÕÐ´ÀÉ½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹ÔéÑ½Àµ™Õ±°É½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹ÔéµÐ´Ä¸ÔÉ½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹Ôé½Ù•É™±½Üµ¡¥‘‘•¸É½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹ÔéÉ½Õ¹‘•µµÉ½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹Ôé‰½É‘•ÈÉ½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹ÔéÍ¡…‘½ÜÉ½ÕÀµ‘…Ñ„µmÙ¥•ÝÁ½ÉÐõ™…±Í•t½¹…Ù¥…Ñ¥½¸µµ•¹Ôé‘ÕÉ…Ñ¥½¸´ÈÀÀ€¨¨é‘…Ñ„µmÍ±½Ðõ¹…Ù¥…Ñ¥½¸µµ•¹Ôµ±¥¹­té™½ÕÌéÉ¥¹œ´À€¨¨é‘…Ñ„µmÍ±½Ðõ¹…Ù¥…Ñ¥½¸µµ•¹Ôµ±¥¹­té™½ÕÌé½ÕÑ±¥¹”µ¹½¹”ˆ°(€€€€€€€±…ÍÍ9…µ”(€€€€€€¥ô(€€€€€ì¸¸¹ÁÉ½ÁÍô(€€€€¼ø(€€¤)ô()™Õ¹Ñ¥½¸9…Ù¥…Ñ¥½¹5•¹ÕY¥•ÝÁ½ÉÐ¡ì(€±…ÍÍ9…µ”°(€€¸¸¹ÁÉ½ÁÌ)ôèI•…Ð¹½µÁ½¹•¹ÑAÉ½ÁÌñÑåÁ•½˜9…Ù¥…Ñ¥½¹5•¹ÕAÉ¥µ¥Ñ¥Ù”¹Y¥•ÝÁ½ÉÐø¤ì(€É•ÑÕÉ¸€ (€€€€ñ‘¥Ø(€€€€€±…ÍÍ9…µ”õí¸ (€€€€€€€€‰…‰Í½±ÕÑ”Ñ½Àµ™Õ±°±•™Ð´À¥Í½±…Ñ”è´ÔÀ™±•à©ÕÍÑ¥™äµ•¹Ñ•Èˆ(€€€€€€¥ô(€€€€ø(€€€€€€ñ9…Ù¥…Ñ¥½¹5•¹ÕAÉ¥µ¥Ñ¥Ù”¹Y¥•ÝÁ½ÉÐ(€€€€€€€‘…Ñ„µÍ±½Ðô‰¹…Ù¥…Ñ¥½¸µµ•¹ÔµÙ¥•ÝÁ½ÉÐˆ(€€€€€€€±…ÍÍ9…µ”õí¸ (€€€€€€€€€€‰½É¥¥¸µÑ½Àµ•¹Ñ•È‰œµÁ½Á½Ù•ÈÑ•áÐµÁ½Á½Ù•Èµ™½É•É½Õ¹‘…Ñ„µmÍÑ…Ñ”õ½Á•¹té…¹¥µ…Ñ”µ¥¸‘…Ñ„µmÍÑ…Ñ”õ±½Í•‘té…¹¥µ…Ñ”µ½ÕÐ‘…Ñ„µmÍÑ…Ñ”õ±½Í•‘téé½½´µ½ÕÐ´äÔ‘…Ñ„µmÍÑ…Ñ”õ½Á•¹téé½½´µ¥¸´äÀÉ•±…Ñ¥Ù”µÐ´Ä¸Ô µmÙ…È ´µÉ…‘¥àµ¹…Ù¥…Ñ¥½¸µµ•¹ÔµÙ¥•ÝÁ½ÉÐµ¡•¥¡Ð¥tÜµ™Õ±°½Ù•É™±½Üµ¡¥‘‘•¸É½Õ¹‘•µµ‰½É‘•ÈÍ¡…‘½ÜµéÜµmÙ…È ´µÉ…‘¥àµ¹…Ù¥…Ñ¥½¸µµ•¹ÔµÙ¥•ÝÁ½ÉÐµÝ¥‘Ñ ¥tˆ°(€€€€€€€€€±…ÍÍ9…µ”(€€€€€€€€¥ô(€€€€€€€ì¸¸¹ÁÉ½ÁÍô(€€€€€€¼ø(€€€€ð½‘¥Øø(€€¤)ô()™Õ¹Ñ¥½¸9…Ù¥…Ñ¥½¹5•¹Õ1¥¹¬¡ì(€±…ÍÍ9…µ”°(€€¸¸¹ÁÉ½ÁÌ)ôèI•…Ð¹½µÁ½¹•¹ÑAÉ½ÁÌñÑåÁ•½˜9…Ù¥…Ñ¥½¹5•¹ÕAÉ¥µ¥Ñ¥Ù”¹1¥¹¬ø¤ì(€É•ÑÕÉ¸€ (€€€€ñ9…Ù¥…Ñ¥½¹5•¹ÕAÉ¥µ¥Ñ¥Ù”¹1¥¹¬(€€€€€‘…Ñ„µÍ±½Ðô‰¹…Ù¥…Ñ¥½¸µµ•¹Ôµ±¥¹¬ˆ(€€€€€±…ÍÍ9…µ”õí¸ (€€€€€€€€‰‘…Ñ„µm…Ñ¥Ù”õÑÉÕ•té™½ÕÌé‰œµ…•¹Ð‘…Ñ„µm…Ñ¥Ù”õÑÉÕ•té¡½Ù•Èé‰œµ…•¹Ð‘…Ñ„µm…Ñ¥Ù”õÑÉÕ•té‰œµ…•¹Ð¼ÔÀ‘…Ñ„µm…Ñ¥Ù”õÑÉÕ•téÑ•áÐµ…•¹Ðµ™½É•É½Õ¹¡½Ù•Èé‰œµ…•¹Ð¡½Ù•ÈéÑ•áÐµ…•¹Ðµ™½É•É½Õ¹™½ÕÌé‰œµ…•¹Ð™½ÕÌéÑ•áÐµ…•¹Ðµ™½É•É½Õ¹™½ÕÌµÙ¥Í¥‰±”éÉ¥¹œµÉ¥¹œ¼ÔÀl™}ÍÙœé¹½Ð¡m±…ÍÌ¨ôÑ•áÐ´t¥téÑ•áÐµµÕÑ•µ™½É•É½Õ¹™±•à™±•àµ½°…À´ÄÉ½Õ¹‘•µÍ´À´ÈÑ•áÐµÍ´ÑÉ…¹Í¥Ñ¥½¸µ…±°½ÕÑ±¥¹”µ¹½¹”™½ÕÌµÙ¥Í¥‰±”éÉ¥¹œµlÍÁát™½ÕÌµÙ¥Í¥‰±”é½ÕÑ±¥¹”´Äl™}ÍÙœé¹½Ð¡m±…ÍÌ¨ôÍ¥é”´t¥téÍ¥é”´Ðˆ°(€€€€€€€±…ÍÍ9…µ”(€€€€€€¥ô(€€€€€ì¸¸¹ÁÉ½ÁÍô(€€€€¼ø(€€¤)ô()™Õ¹Ñ¥½¸9…Ù¥…Ñ¥½¹5•¹Õ%¹‘¥…Ñ½È¡ì(€±…ÍÍ9…µ”°(€€¸¸¹ÁÉ½ÁÌ)ôèI•…Ð¹½µÁ½¹•¹ÑAÉ½ÁÌñÑåÁ•½˜9…Ù¥…Ñ¥½¹5•¹ÕAÉ¥µ¥Ñ¥Ù”¹%¹‘¥…Ñ½Èø¤ì(€É•ÑÕÉ¸€ (€€€€ñ9…Ù¥…Ñ¥½¹5•¹ÕAÉ¥µ¥Ñ¥Ù”¹%¹‘¥…Ñ½È(€€€€€‘…Ñ„µÍ±½Ðô‰¹…Ù¥…Ñ¥½¸µµ•¹Ôµ¥¹‘¥…Ñ½Èˆ(€€€€€±…ÍÍ9…µ”õí¸ (€€€€€€€€‰‘…Ñ„µmÍÑ…Ñ”õÙ¥Í¥‰±•té…¹¥µ…Ñ”µ¥¸‘…Ñ„µmÍÑ…Ñ”õ¡¥‘‘•¹té…¹¥µ…Ñ”µ½ÕÐ‘…Ñ„µmÍÑ…Ñ”õ¡¥‘‘•¹té™…‘”µ½ÕÐ‘…Ñ„µmÍÑ…Ñ”õÙ¥Í¥‰±•té™…‘”µ¥¸Ñ½Àµ™Õ±°èµlÅt™±•à ´Ä¸Ô¥Ñ•µÌµ•¹©ÕÍÑ¥™äµ•¹Ñ•È½Ù•É™±½Üµ¡¥‘‘•¸ˆ°(€€€€€€€±…ÍÍ9…µ”(€€€€€€¥ô(€€€€€ì¸¸¹ÁÉ½ÁÍô(€€€€ø(€€€€€€ñ‘¥Ø±…ÍÍ9…µ”ô‰‰œµ‰½É‘•ÈÉ•±…Ñ¥Ù”Ñ½ÀµlØÀ•t ´ÈÜ´ÈÉ½Ñ…Ñ”´ÐÔÉ½Õ¹‘•µÑ°µÍ´Í¡…‘½Üµµˆ€¼ø(€€€€ð½9…Ù¥…Ñ¥½¹5•¹ÕAÉ¥µ¥Ñ¥Ù”¹%¹‘¥…Ñ½Èø(€€¤)ô()•áÁ½ÉÐì(€9…Ù¥…Ñ¥½¹5•¹Ô°(€9…Ù¥…Ñ¥½¹5•¹Õ1¥ÍÐ°(€9…Ù¥…Ñ¥½¹5•¹Õ%Ñ•´°(€9…Ù¥…Ñ¥½¹5•¹Õ½¹Ñ•¹Ð°(€9…Ù¥…Ñ¥½¹5•¹ÕQÉ¥•È°(€9…Ù¥…Ñ¥½¹5•¹Õ1¥¹¬°(€9…Ù¥…Ñ¥½¹5•¹Õ%¹‘¥…Ñ½È°(€9…Ù¥…Ñ¥½¹5•¹ÕY¥•ÝÁ½ÉÐ°(€¹…Ù¥…Ñ¥½¹5•¹ÕQÉ¥•ÉMÑå±”°)ô(