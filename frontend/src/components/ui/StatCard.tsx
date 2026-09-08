import React from 'react';
import { GlassCard } from './GlassCard';
import { formatMoney, formatCashFlow, formatSpend } from '@/lib/money';
import { AlertCircle, TrendingUp, TrendingDown, DollarSign } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface StatCardProps {
  title: string;
  spend?: string | null;
  netCashFlow?: string | null;
  purchases?: string | null;
  refunds?: string | null;
  total?: string | null;
  count?: number | null;
  matchCount?: number | null;
  truncated?: boolean;
  onClick?: () => void;
  className?: string;
  leadWithNetCashFlow?: boolean;
}

export function StatCard({
  title,
  spend,
  netCashFlow,
  purchases,
  refunds,
  total,
  count,
  matchCount,
  truncated,
  onClick,
  className,
  leadWithNetCashFlow = false,
}: StatCardProps) {
  // Primary figure: spend (purchases - refunds) or netCashFlow
  const primaryAmount = leadWithNetCashFlow ? netCashFlow : (spend ?? total);
  const primaryLabel = leadWithNetCashFlow ? 'Net Cash Flow' : 'Spend (Purchases − Refunds)';

  return (
    <GlassCard
      className={cn('relative overflow-hidden', onClick && 'cursor-pointer', className)}
      interactive={Boolean(onClick)}
      onClick={onClick}
    >
      <div className="flex items-start justify-between">
        <div>
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {title}
          </p>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-bold tracking-tight text-foreground">
              {leadWithNetCashFlow ? formatCashFlow(primaryAmount) : formatSpend(primaryAmount)}
            </span>
            <span className="text-xs text-muted-foreground">{primaryLabel}</span>
          </div>
        </div>
        <div className="rounded-full bg-secondary/80 p-2 text-muted-foreground">
          <DollarSign className="h-5 w-5" />
        </div>
      </div>

      {/* Sub-breakdowns with explicit SPEND vs spend distinction */}
      <div className="mt-4 grid grid-cols-2 gap-2 border-t border-border/40 pt-3 text-xs">
        {netCashFlow !== undefined && !leadWithNetCashFlow && (
          <div>
            <span className="text-muted-foreground">Net Cash Flow: </span>
            <span className="font-semibold text-foreground">{formatCashFlow(netCashFlow)}</span>
          </div>
        )}
        {purchases !== undefined && (
          <div>
            <span className="text-muted-foreground">Purchases (SPEND): </span>
            <span className="font-semibold text-foreground">{formatSpend(purchases)}</span>
          </div>
        )}
        {refunds !== undefined && (
          <div>
            <span className="text-muted-foreground">Refunds: </span>
            <span className="font-semibold text-foreground">{formatMoney(refunds)}</span>
          </div>
        )}
        {count !== undefined && count !== null && (
          <div>
            <span className="text-muted-foreground">Transactions: </span>
            <span className="font-semibold text-foreground">{count}</span>
          </div>
        )}
      </div>

      {/* Truncation warning indicator */}
      {truncated && (
        <div className="mt-3 flex items-center gap-1.5 rounded bg-amber-500/10 px-2 py-1 text-[11px] font-medium text-amber-300">
          <AlertCircle className="h-3.5 w-3.5 flex-shrink-0" />
          <span>
            Showing sample of {count ?? 'partial'} rows (Total matches: {matchCount ?? 'unknown'}).
          </span>
        </div>
      )}
    </GlassCard>
  );
}
