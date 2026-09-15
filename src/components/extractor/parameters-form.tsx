'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Table2, Tags, Lightbulb } from 'lucide-react';

interface ParametersFormProps {
  tableTitle: string;
  onTableTitleChange: (v: string) => void;
  termsText: string;
  onTermsTextChange: (v: string) => void;
  contextHint: string;
  onContextHintChange: (v: string) => void;
  disabled?: boolean;
}

export function ParametersForm({
  tableTitle,
  onTableTitleChange,
  termsText,
  onTermsTextChange,
  contextHint,
  onContextHintChange,
  disabled,
}: ParametersFormProps) {
  // Contar términos: uno por línea o separados por coma
  const termsList = termsText
    .split(/[\n,]/)
    .map((t) => t.trim())
    .filter((t) => t.length > 0);

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Table2 className="size-4 text-primary" />
            Título de la Tabla o Sección
          </CardTitle>
          <CardDescription>
            Indique el título exacto o aproximado de la tabla/sección a buscar dentro del PDF.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-2">
            <Label htmlFor="table-title" className="text-xs text-muted-foreground">
              Título / Identificador
            </Label>
            <Input
              id="table-title"
              value={tableTitle}
              onChange={(e) => onTableTitleChange(e.target.value)}
              placeholder='Ej: "Tabla 4.2 - Propiedades Mecánicas"'
              disabled={disabled}
              className="text-sm"
            />
            <p className="text-xs text-muted-foreground">
              El sistema buscará coincidencias parciales en el contenido del PDF.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Tags className="size-4 text-primary" />
            Términos a Catalogar
          </CardTitle>
          <CardDescription>
            Liste cada término en una línea nueva o separado por comas. La IA buscará el valor asociado a cada uno.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-col gap-2">
            <Label htmlFor="terms-input" className="text-xs text-muted-foreground">
              Términos (uno por línea o separados por coma)
            </Label>
            <Textarea
              id="terms-input"
              value={termsText}
              onChange={(e) => onTermsTextChange(e.target.value)}
              placeholder={'Módulo de elasticidad\nResistencia a la compresión\nTensión admisible\nDensidad'}
              disabled={disabled}
              rows={6}
              className="resize-y text-sm font-mono"
            />
            <div className="flex items-center justify-between">
              <p className="text-xs text-muted-foreground">
                Ejemplo: &ldquo;Módulo de elasticidad&rdquo;, &ldquo;Resistencia a la compresión&rdquo;
              </p>
              <Badge variant={termsList.length > 0 ? 'default' : 'secondary'} className="text-[10px]">
                {termsList.length} {termsList.length === 1 ? 'término' : 'términos'}
              </Badge>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Lightbulb className="size-4 text-primary" />
            Pista de Contexto <span className="text-xs font-normal text-muted-foreground">(opcional)</span>
          </CardTitle>
          <CardDescription>
            Información adicional para ayudar al modelo a ubicar la tabla (página, capítulo, contexto cercano).
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Input
            value={contextHint}
            onChange={(e) => onContextHintChange(e.target.value)}
            placeholder='Ej: "Sección 4, página 87, capítulo de materiales"'
            disabled={disabled}
            className="text-sm"
          />
        </CardContent>
      </Card>
    </div>
  );
}
