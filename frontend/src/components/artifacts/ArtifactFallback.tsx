import React from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { HelpCircle, ExternalLink } from 'lucide-react';

export interface ArtifactFallbackProps {
  artifact?: {
    id?: number;
    kind?: string;
    title?: string;
    digest?: any;
  };
  onOpen?: (id: number) => void;
}

export function ArtifactFallback({ artifact, onOpen }: ArtifactFallbackProps) {
  const digestText =
    typeof artifact?.digest === 'string'
      ? artifact.digest
      : artifact?.digest?.text || JSON.stringify(artifact?.digest || {});

  return (
    <GlassCard variant="subtle" className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <HelpCircle className="h-4 w-4 text-muted-foreground" />
          <h4 className="text-sm font-medium text-foreground">
            {artifact?.title || `Artifact #${artifact?.id ?? 'unknown'}`}
          </h4>
          <span className="rounded bg-secondary px-1.5 py-0.5 text-[10px] text-muted-foreground">
            kind: {artifact?.kind || 'unknown'}
          </span>
        </div>

        {artifact?.id && onOpen && (
          <button
            onClick={() => onOpen(artifact.id!)}
            className="flex items-center gap-1 text-xs text-primary hover:underline"
          >
            <span>Open Artifact</span>
            <ExternalLink className="h-3 w-3" />
          </button>
        )}
      </div>

      <div className="rounded bg-background/50 p-2.5 font-mono text-xs text-muted-foreground whitespace-pre-wrap">
        {digestText || 'No digest text provided for this artifact.'}
      </div>
    </GlassCard>
  );
}
