import React from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { AlertCircle } from 'lucide-react';

export interface CatalogListProps {
  artifact?: {
    id?: number;
    title?: string;
    digest?: any;
    data?: any;
  };
  values?: any[];
  matchCount?: number;
  truncated?: boolean;
}

export function CatalogList({
  artifact,
  values: propValues,
  matchCount: propMatchCount,
  truncated: propTruncated,
}: CatalogListProps) {
  const digestData = artifact?.digest || {};
  const data = artifact?.data || {};
  const values = propValues || data.values || digestData.values || digestData.sample_items || [];
  const matchCount = propMatchCount ?? data.match_count ?? digestData.match_count ?? values.length;
  const isTruncated = propTruncated ?? data.truncated ?? digestData.truncated ?? false;

  return (
    <GlassCard className="flex flex-col gap-3">
      <div>
        <h3 className="font-semibold text-foreground">
          {artifact?.title || 'Catalog Values'}
        </h3>
        <p className="text-xs text-muted-foreground">
          Unique labels and occurrences
        </p>
      </div>

      {isTruncated && (
        <div className="flex items-center gap-2 rounded bg-amber-500/10 px-3 py-1.5 text-xs text-amber-300">
          <AlertCircle className="h-4 w-4 flex-shrink-0" />
          <span>
            Partial catalog shown ({values.length} of {matchCount} total labels).
          </span>
        </div>
      )}

      <div className="flex flex-wrap gap-2 pt-2">
        {values.map((v: any, idx: number) => {
          const label = typeof v === 'string' ? v : v.value || v.name;
          const count = typeof v === 'object' ? v.count : undefined;

          return (
            <div
              key={idx}
              className="flex items-center gap-1.5 rounded-md border border-border/60 bg-secondary/40 px-2.5 py-1 text-xs"
            >
              <span className="font-medium text-foreground">{label}</span>
              {count !== undefined && (
                <span className="rounded-full bg-muted px-1.5 py-0.2 text-[10px] text-muted-foreground">
                  {count}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </GlassCard>
  );
}
