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
        setError('El archivo debe ser un PDF válido.');
        return;
      }
      if (file.size > 50 * 1024 * 1024) {
        setError('El archivo excede el tamaño máximo de 50 MB.');
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
          Seleccione o arrastre el PDF desde el cual se extraerán los datos.
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
                Haga clic o arrastre un archivo PDF aquí
              </p>
              <p className="text-xs text-muted-foreground">
                Formato PDF · Máximo 50 MB
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
              variant="ghost"
              size="icon"
              onClick={handleRemove}
              disabled={disabled}
              aria-label="Quitar archivo"
              className="ml-2 shrink-0"
            >
              <X className="size-4" />
            </Button>
          </div>
        )}

        {error && (
          <div className="mt-3 flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <AlertCircle className="size-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
