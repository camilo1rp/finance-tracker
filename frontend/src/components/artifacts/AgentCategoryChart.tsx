import React from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { formatMoney, formatSpend } from '@/lib/money';
import { AlertCircle } from 'lucide-react';

export interface AgentCategoryChartProps {
  artifact?: {
    id?: number;
    title?: string;
    digest?: any;
    data?: any;
  };
  groups?: any[];
  matchCount?: number;
  truncated?: boolean;
}

export function AgentCategoryChart({
  artifact,
  groups: propGroups,
  matchCount: propMatchCount,
  truncated: propTruncated,
}: AgentCategoryChartProps) {
  const digestData = artifact?.digest || {};
  const data = artifact?.data || {};
  const groups = propGroups || data.groups || digestData.groups || digestData.sample_items || [];
  const matchCount = propMatchCount ?? data.match_count ?? digestData.match_count ?? groups.length;
  const isTruncated = propTruncated ?? data.truncated ?? digestData.truncated ?? false;

  return (
    <GlassCard className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-foreground">
            {artifact?.title || 'Group Summary'}
          </h3>
          <p className="text-xs text-muted-foreground">
            Ranked by spend (Purchases − Refunds)
          </p>
        </div>
      </div>

      {isTruncated && (
        <div className="flex items-center gap-2 rounded bg-amber-500/10 px-3 py-1.5 text-xs text-amber-300">
          <AlertCircle className="h-4 w-4 flex-shrink-0" />
          <span>
            Partial set displayed ({groups.length} groups of {matchCount} matches).
          </span>
        </div>
      )}

      <div className="flex flex-col divide-y divide-border/30">
        {groups.map((group: any, idx: number) => {
          const groupName = group.group_value || group.name || group.category || `Item ${idx + 1}`;
          const spend = group.spend ?? group.total;
          const purchases = group.purchases;
          const refunds = group.refunds;

          return (
            <div key={idx} className="flex items-center justify-between py-2 text-sm">
              <div className="flex flex-col">
                <span className="font-medium text-foreground">{groupName}</span>
                {(purchases !== undefined || refunds !== undefined) && (
                  <span className="text-[11px] text-muted-foreground">
                    Purchases: {formatSpend(purchases)} | Refunds: {formatMoney(refunds)}
                  </span>
                )}
              </div>
              <div className="text-right">
                <div className="font-mono font-semibold text-foreground">
                  {formatSpend(spend)}
                </div>
                <div className="text-[10px] text-muted-foreground">spend</div>
              </div>
            </div>
          );
        })}
      </div>
    </GlassCard>
  );
}
