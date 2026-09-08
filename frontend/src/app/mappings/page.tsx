'use client';

import React, { useState } from 'react';
import { PageLayout } from '@/components/ui/PageLayout';
import { GlassCard } from '@/components/ui/GlassCard';
import { DataTable, Column } from '@/components/ui/DataTable';
import {
  useMappingsList,
  useCreateMapping,
  usePreviewMappingPlan,
  useApplyMappingPlan,
  usePatchMapping,
  useDeleteMapping,
} from '@/api/hooks/useMappings';
import { useAnalyticsUnmapped } from '@/api/hooks/useAnalytics';
import { useAccountsList } from '@/api/hooks/useAccounts';
import {
  NormalizationMappingOut,
  MappingPlanIn,
  MappingPreview,
  ApplyResult,
} from '@/api/generated/model';
import {
  Sliders,
  Plus,
  Play,
  Trash2,
  Edit2,
  AlertCircle,
  CheckCircle2,
  Layers,
  HelpCircle,
  RefreshCw,
} from 'lucide-react';

export default function MappingsPage() {
  const { data: accounts = [] } = useAccountsList();
  const [selectedAccountId, setSelectedAccountId] = useState<number | undefined>();
  const [kindFilter, setKindFilter] = useState<string>('');

  const { data: mappings = [], isLoading: mappingsLoading, refetch: refetchMappings } = useMappingsList(
    selectedAccountId,
    kindFilter || undefined
  );
  const { data: unmappedData, isLoading: unmappedLoading } = useAnalyticsUnmapped();

  const createMappingMutation = useCreateMapping();
  const previewPlanMutation = usePreviewMappingPlan();
  const applyPlanMutation = useApplyMappingPlan();
  const patchMappingMutation = usePatchMapping();
  const deleteMappingMutation = useDeleteMapping();

  // Create form state
  const [formKind, setFormKind] = useState<'category' | 'merchant' | 'account'>('category');
  const [rawPattern, setRawPattern] = useState('');
  const [canonicalValue, setCanonicalValue] = useState('');
  const [formAccountId, setFormAccountId] = useState<number | ''>('');
  const [chainReclassify, setChainReclassify] = useState(true);
  const [createMessage, setCreateMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  // Preview & Apply results
  const [previewResult, setPreviewResult] = useState<MappingPreview | null>(null);
  const [applyResult, setApplyResult] = useState<ApplyResult | null>(null);

  const handleCreateRule = async (e: React.FormEvent) => {
    e.preventDefault();
    setCreateMessage(null);
    if (!canonicalValue.trim()) {
      setCreateMessage({ type: 'error', text: 'Canonical value is required.' });
      return;
    }

    try {
      const res = await createMappingMutation.mutateAsync({
        data: {
          kind: formKind,
          raw_value: rawPattern.trim() || null,
          canonical_value: canonicalValue.trim(),
          account_id: formAccountId ? Number(formAccountId) : null,
        },
        chainReclassify,
      });

      setCreateMessage({
        type: 'success',
        text: `Created rule #${res.mapping.id} (${res.mapping.kind}: "${res.mapping.raw_value || 'null'}" → "${res.mapping.canonical_value}"). ${res.reclassified ? 'Reclassified ledger transactions.' : 'No reclassify triggered.'}`,
      });
      setRawPattern('');
      setCanonicalValue('');
    } catch (err: any) {
      setCreateMessage({
        type: 'error',
        text: err.response?.data?.detail || err.message || 'Failed to create rule.',
      });
    }
  };

  const handleDeleteRule = async (id: number) => {
    if (!confirm(`Delete rule #${id}? This will reclassify matching transactions.`)) return;
    try {
      const res = await deleteMappingMutation.mutateAsync(id);
      alert(`Rule deleted. Reclassification updated ${res.reclass_updated} transactions.`);
    } catch (err: any) {
      alert(`Error deleting rule: ${err.message}`);
    }
  };

  const handleQuickAddUnmapped = (kind: 'category' | 'merchant', raw: string) => {
    setFormKind(kind);
    setRawPattern(raw);
    setCanonicalValue(raw);
  };

  const columns: Column<NormalizationMappingOut>[] = [
    { header: 'ID', accessorKey: 'id', className: 'w-12 font-mono text-xs text-muted-foreground' },
    {
      header: 'Kind',
      cell: (m) => (
        <span className="rounded bg-secondary/80 px-2 py-0.5 text-xs uppercase font-medium">
          {m.kind}
        </span>
      ),
    },
    {
      header: 'Raw Pattern (Pattern)',
      cell: (m) => (
        <span className="font-mono text-xs text-foreground">
          {m.raw_value ? `"${m.raw_value}"` : '<null pattern>'}
        </span>
      ),
    },
    {
      header: 'Canonical Value',
      cell: (m) => <span className="font-semibold text-foreground">{m.canonical_value}</span>,
    },
    {
      header: 'Scope',
      cell: (m) => (
        <span className="text-xs text-muted-foreground">
          {m.account_id ? `Account #${m.account_id}` : 'Global'}
        </span>
      ),
    },
    {
      header: 'Actions',
      align: 'right',
      cell: (m) => (
        <div className="flex items-center justify-end gap-2">
          <button
            onClick={() => handleDeleteRule(m.id)}
            className="p-1 text-muted-foreground hover:text-rose-400 transition-colors"
            title="Delete rule and reclassify"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      ),
    },
  ];

  return (
    <PageLayout
      title="Mappings & Rules Workbench"
      description="Manage categorization rules, inspect unmapped worklists, and execute reclassifications"
      breadcrumbs={[{ label: 'Mappings' }]}
    >
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 Cols: Unmapped Worklist + Rules Table */}
        <div className="lg:col-span-2 space-y-6">
          {/* Unmapped Worklist from GET /analytics/unmapped */}
          <GlassCard variant="warning" className="border-amber-600/30">
            <div className="flex items-center justify-between mb-2">
              <h3 className="font-semibold text-amber-200 flex items-center gap-2">
                <AlertCircle className="h-4 w-4" />
                <span>Unmapped Worklist (Ledger-Wide)</span>
              </h3>
              <span className="text-xs text-muted-foreground">
                Matches: {(unmappedData?.categories?.length ?? 0) + (unmappedData?.merchants?.length ?? 0)}
              </span>
            </div>
            <p className="text-xs text-muted-foreground mb-3">
              Transactions missing normalized categories or merchants. Click any item to stage a mapping rule:
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <span className="text-xs font-semibold text-foreground block mb-2">
                  Unmapped Categories:
                </span>
                <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto">
                  {unmappedData?.categories?.map((cat, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => handleQuickAddUnmapped('category', cat)}
                      className="flex items-center gap-1 rounded bg-secondary/80 px-2 py-0.5 text-xs text-foreground hover:bg-primary/20 hover:text-primary transition-colors border border-border/40"
                    >
                      <span>{cat}</span>
                    </button>
                  ))}
                  {(!unmappedData?.categories || unmappedData.categories.length === 0) && (
                    <span className="text-xs text-muted-foreground">All categories mapped!</span>
                  )}
                </div>
              </div>

              <div>
                <span className="text-xs font-semibold text-foreground block mb-2">
                  Unmapped Merchants:
                </span>
                <div className="flex flex-wrap gap-1.5 max-h-36 overflow-y-auto">
                  {unmappedData?.merchants?.map((m, i) => (
                    <button
                      key={i}
                      type="button"
                      onClick={() => handleQuickAddUnmapped('merchant', m)}
                      className="flex items-center gap-1 rounded bg-secondary/80 px-2 py-0.5 text-xs text-foreground hover:bg-primary/20 hover:text-primary transition-colors border border-border/40"
                    >
                      <span>{m}</span>
                    </button>
                  ))}
                  {(!unmappedData?.merchants || unmappedData.merchants.length === 0) && (
                    <span className="text-xs text-muted-foreground">All merchants mapped!</span>
                  )}
                </div>
              </div>
            </div>
          </GlassCard>

          {/* Rules Table with Account Scope and Filter */}
          <GlassCard>
            <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
              <h3 className="font-semibold text-foreground flex items-center gap-2">
                <Sliders className="h-4 w-4 text-primary" />
                <span>Active Normalization Rules ({mappings.length})</span>
              </h3>

              <div className="flex items-center gap-2">
                <select
                  value={selectedAccountId ?? ''}
                  onChange={(e) => setSelectedAccountId(e.target.value ? Number(e.target.value) : undefined)}
                  className="rounded border border-border bg-secondary/40 px-2 py-1 text-xs text-foreground"
                >
                  <option value="">All Scopes (Global + Accounts)</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      Account: {a.name}
                    </option>
                  ))}
                </select>

                <select
                  value={kindFilter}
                  onChange={(e) => setKindFilter(e.target.value)}
                  className="rounded border border-border bg-secondary/40 px-2 py-1 text-xs text-foreground"
                >
                  <option value="">All Kinds</option>
                  <option value="category">Category</option>
                  <option value="merchant">Merchant</option>
                  <option value="account">Account</option>
                </select>
              </div>
            </div>

            <DataTable
              data={mappings}
              columns={columns}
              keyExtractor={(m) => m.id}
              emptyMessage={mappingsLoading ? 'Loading mapping rules...' : 'No rules match filter.'}
            />
          </GlassCard>
        </div>

        {/* Right Col: Create Rule Form */}
        <div>
          <GlassCard className="space-y-4">
            <h3 className="font-semibold text-foreground flex items-center gap-2">
              <Plus className="h-4 w-4 text-primary" />
              <span>Create Normalization Rule</span>
            </h3>

            {createMessage && (
              <div
                className={`rounded p-2.5 text-xs flex items-center gap-2 ${
                  createMessage.type === 'success'
                    ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-300'
                    : 'bg-rose-500/10 border border-rose-500/30 text-rose-300'
                }`}
              >
                {createMessage.type === 'success' ? (
                  <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
                ) : (
                  <AlertCircle className="h-4 w-4 flex-shrink-0" />
                )}
                <span>{createMessage.text}</span>
              </div>
            )}

            <form onSubmit={handleCreateRule} className="space-y-3">
              <div>
                <label className="text-xs font-medium text-foreground block mb-1">Rule Kind</label>
                <div className="flex gap-4 text-xs">
                  {(['category', 'merchant', 'account'] as const).map((k) => (
                    <label key={k} className="flex items-center gap-1 cursor-pointer capitalize">
                      <input
                        type="radio"
                        name="ruleKind"
                        checked={formKind === k}
                        onChange={() => setFormKind(k)}
                      />
                      <span>{k}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <label className="text-xs font-medium text-foreground block mb-1">
                  Raw Pattern (Supports % Wildcard)
                </label>
                <input
                  type="text"
                  placeholder="e.g. UBER%TRIP or TRADER JOE"
                  value={rawPattern}
                  onChange={(e) => setRawPattern(e.target.value)}
                  className="w-full font-mono rounded-md border border-border bg-secondary/40 px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
                <p className="mt-1 text-[10px] text-muted-foreground">
                  Case-insensitive LIKE matching. Leave empty or enter pattern.
                </p>
              </div>

              <div>
                <label className="text-xs font-medium text-foreground block mb-1">
                  Canonical Replacement Value *
                </label>
                <input
                  type="text"
                  placeholder="e.g. Rideshare, Groceries"
                  value={canonicalValue}
                  onChange={(e) => setCanonicalValue(e.target.value)}
                  className="w-full rounded-md border border-border bg-secondary/40 px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-foreground block mb-1">
                  Account Scope
                </label>
                <select
                  value={formAccountId}
                  onChange={(e) => setFormAccountId(e.target.value ? Number(e.target.value) : '')}
                  className="w-full rounded-md border border-border bg-secondary/40 px-2 py-1.5 text-xs text-foreground focus:outline-none"
                >
                  <option value="">Global (Applies across all accounts)</option>
                  {accounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      Account: {a.name} (•••• {a.last4})
                    </option>
                  ))}
                </select>
              </div>

              <div className="rounded-md border border-border/40 bg-secondary/20 p-2.5">
                <label className="flex items-start gap-2 cursor-pointer text-xs">
                  <input
                    type="checkbox"
                    checked={chainReclassify}
                    onChange={(e) => setChainReclassify(e.target.checked)}
                    className="mt-0.5 h-4 w-4 rounded border-border"
                  />
                  <div>
                    <span className="font-semibold text-foreground">
                      Trigger Immediate Reclassification
                    </span>
                    <p className="text-[10px] text-muted-foreground leading-normal mt-0.5">
                      Plain POST /mappings does NOT reclassify (INV-07). Checking this chains POST /transactions/reclassify and invalidates ledger analytics.
                    </p>
                  </div>
                </label>
              </div>

              <button
                type="submit"
                disabled={createMappingMutation.isPending}
                className="w-full rounded-lg bg-primary py-2 text-xs font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                {createMappingMutation.isPending ? 'Saving & Reclassifying...' : 'Add Mapping Rule'}
              </button>
            </form>
          </GlassCard>
        </div>
      </div>
    </PageLayout>
  );
}
