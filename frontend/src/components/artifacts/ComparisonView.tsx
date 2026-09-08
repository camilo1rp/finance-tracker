import React from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { formatMoney, formatSpend } from '@/lib/money';

export interface ComparisonViewProps {
  artifact?: {
    id?: number;
    title?: string;
    spec?: any;
    digest?: any;
  };
  comparisonData?: any;
}

export function ComparisonView({ artifact, comparisonData }: ComparisonViewProps) {
  const digestData = artifact?.digest || {};
  const data = comparisonData || digestData.comparison || digestData;

  const title = artifact?.title || 'Period Comparison';
  const periodA = data.period_a || { label: 'Period A', spend: '0.00' };
  const periodB = data.period_b || { label: 'Period B', spend: '0.00' };
  const delta = data.delta || '0.00';

  return (
    <GlassCard className="flex flex-col gap-4">
      <div>
        <h3 className="font-semibold text-foreground">{title}</h3>
        <p className="text-xs text-muted-foreground">Multi-window spending comparison</p>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-lg border border-border/40 bg-secondary/30 p-3">
          <span className="text-xs text-muted-foreground">{periodA.label || 'Base Period'}</span>
          <div className="mt-1 font-mono text-xl font-bold text-foreground">
            {formatSpend(periodA.spend)}
          </div>
        </div>

        <div className="rounded-lg border border-border/40 bg-secondary/30 p-3">
          <span className="text-xs text-muted-foreground">{periodB.label || 'Target Period'}</span>
          <div className="mt-1 font-mono text-xl font-bold text-foreground">
            {formatSpend(periodB.spend)}
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between border-t border-border/30 pt-2 text-xs">
        <span className="text-muted-foreground">Net Delta:</span>
        <span className="font-mono font-semibold text-foreground">{formatMoney(delta)}</span>
      </div>
    </GlassCard>
  );
}
