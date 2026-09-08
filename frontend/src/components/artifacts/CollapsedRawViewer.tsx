import React, { useState } from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { ChevronDown, ChevronRight, FileText } from 'lucide-react';

export interface CollapsedRawViewerProps {
  artifact?: {
    id?: number;
    title?: string;
    spec?: any;
    digest?: any;
    data?: any;
  };
  rawPayload?: any;
}

export function CollapsedRawViewer({ artifact, rawPayload }: CollapsedRawViewerProps) {
  const [isExpanded, setIsExpanded] = useState(false);
  const data = rawPayload || artifact?.data || artifact?.digest?.raw || artifact?.digest || {};
  const formattedJson = typeof data === 'string' ? data : JSON.stringify(data, null, 2);

  return (
    <GlassCard className="flex flex-col gap-2">
      <div
        className="flex cursor-pointer items-center justify-between"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="flex items-center gap-2">
          <FileText className="h-4 w-4 text-muted-foreground" />
          <h3 className="text-sm font-medium text-foreground">
            {artifact?.title || 'Raw Output (Collapsed)'}
          </h3>
        </div>
        <button
          type="button"
          className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <span>{isExpanded ? 'Collapse' : 'Expand'}</span>
          {isExpanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>
      </div>

      {isExpanded && (
        <pre className="mt-2 max-h-72 overflow-x-auto rounded bg-secondary/50 p-3 font-mono text-xs text-secondary-foreground">
          {formattedJson}
        </pre>
      )}
    </GlassCard>
  );
}
