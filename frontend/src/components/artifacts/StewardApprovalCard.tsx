import React, { useState } from 'react';
import { GlassCard } from '@/components/ui/GlassCard';
import { AlertCircle, CheckCircle, XCircle, ShieldAlert } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface StewardApprovalCardProps {
  artifact?: {
    id?: number;
    title?: string;
    spec?: any;
    digest?: any;
    data?: any;
  };
  interrupt?: {
    ops: any[];
    preview: any;
    preview_artifact_id?: number | null;
    rationale?: string | null;
  };
  onApprove?: (ops: any[]) => void;
  onReject?: () => void;
  disabled?: boolean;
}

export function StewardApprovalCard({
  artifact,
  interrupt,
  onApprove,
  onReject,
  disabled = false,
}: StewardApprovalCardProps) {
  const ops = interrupt?.ops || artifact?.data?.ops || artifact?.spec?.ops || [];
  const preview = interrupt?.preview || artifact?.data?.preview || artifact?.data || artifact?.digest?.preview || {};
  const rationale = interrupt?.rationale || artifact?.spec?.rationale;

  // Selected indices for subset approval
  const [selectedIndices, setSelectedIndices] = useState<Set<number>>(
    () => new Set(ops.map((_: any, idx: number) => idx))
  );

  const toggleOp = (idx: number) => {
    if (disabled) return;
    const next = new Set(selectedIndices);
    if (next.has(idx)) {
      next.delete(idx);
    } else {
      next.add(idx);
    }
    setSelectedIndices(next);
  };

  const handleApprove = () => {
    if (!onApprove) return;
    const subsetOps = ops.filter((_: any, idx: number) => selectedIndices.has(idx));
    onApprove(subsetOps);
  };

  const impacts = preview.ops || [];
  const conflicts = impacts.filter((imp: any) => imp.conflicts_with_existing_id != null);

  return (
    <GlassCard variant="warning" className="flex flex-col gap-4 border-amber-600/40">
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <ShieldAlert className="h-5 w-5 text-amber-400" />
            <h3 className="font-semibold text-amber-200">
              Steward Approval Required
            </h3>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {rationale || 'Review proposed mapping rules and transaction overrides before applying.'}
          </p>
        </div>
        <span className="rounded bg-amber-500/20 px-2 py-0.5 text-[11px] font-medium text-amber-300">
          Pending
        </span>
      </div>

      {conflicts.length > 0 && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive-foreground">
          <div className="flex items-center gap-1.5 font-semibold text-rose-300">
            <AlertCircle className="h-4 w-4" />
            <span>Conflicts Detected ({conflicts.length})</span>
          </div>
          <p className="mt-1 text-[11px] text-muted-foreground">
            Some ops conflict with existing mapping rules. Deselect conflicting items or convert to updates.
          </p>
        </div>
      )}

      {/* Operations List */}
      <div className="flex flex-col gap-2">
        <span className="text-xs font-semibold text-muted-foreground">
          Proposed Operations ({selectedIndices.size} of {ops.length} selected):
        </span>
        <div className="max-h-60 overflow-y-auto divide-y divide-border/20 rounded-md border border-border/40 bg-card/60 p-2 text-xs">
          {ops.map((op: any, idx: number) => {
            const isSelected = selectedIndices.has(idx);
            const impact = impacts.find((imp: any) => imp.index === idx) || {};
            const hasConflict = impact.conflicts_with_existing_id != null;

            return (
              <div
                key={idx}
                onClick={() => toggleOp(idx)}
                className={cn(
                  'flex items-center justify-between p-2 cursor-pointer rounded transition-colors',
                  isSelected ? 'bg-secondary/40' : 'opacity-50 hover:opacity-80',
                  hasConflict && 'border-l-2 border-rose-500 bg-destructive/5'
                )}
              >
                <div className="flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => {}} // handled by parent div
                    disabled={disabled}
                    className="h-4 w-4 rounded border-border"
                  />
                  <div>
                    <span className="font-semibold uppercase text-amber-300">
                      [{op.op || 'create'}]
                    </span>{' '}
                    <span className="font-mono text-foreground">
                      {op.raw_value ? `"${op.raw_value}"` : 'null'} → "{op.canonical_value}"
                    </span>
                    {op.account_id && (
                      <span className="ml-1 text-[10px] text-muted-foreground">
                        (account: {op.account_id})
                      </span>
                    )}
                  </div>
                </div>

                <div className="text-right text-[11px] text-muted-foreground">
                  {impact.would_change != null && (
                    <span>Would change: {impact.would_change} rows</span>
                  )}
                  {hasConflict && (
                    <span className="ml-2 font-semibold text-rose-400">
                      Conflicts with #{impact.conflicts_with_existing_id}
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Action Buttons */}
      {(onApprove || onReject) && (
        <div className="flex items-center justify-end gap-3 pt-2">
          {onReject && (
            <button
              type="button"
              onClick={onReject}
              disabled={disabled}
              className="flex items-center gap-1.5 rounded-lg border border-border/80 bg-secondary/60 px-3 py-1.5 text-xs font-medium text-foreground hover:bg-secondary disabled:opacity-50"
            >
              <XCircle className="h-4 w-4" />
              <span>Reject All</span>
            </button>
          )}
          {onApprove && (
            <button
              type="button"
              onClick={handleApprove}
              disabled={disabled || selectedIndices.size === 0}
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              <CheckCircle className="h-4 w-4" />
              <span>
                Approve {selectedIndices.size < ops.length ? `Selected (${selectedIndices.size})` : 'All'}
              </span>
            </button>
          )}
        </div>
      )}
    </GlassCard>
  );
}
