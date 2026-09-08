'use client';

import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { PageLayout } from '@/components/ui/PageLayout';
import { GlassCard } from '@/components/ui/GlassCard';
import { DataTable, Column } from '@/components/ui/DataTable';
import { useArtifactsList, useDismissArtifact } from '@/api/hooks/useArtifacts';
import { ArtifactSummary } from '@/api/generated/model';
import { FileBarChart, Filter, Trash2, ExternalLink, ChevronRight } from 'lucide-react';

export default function ArtifactsPage() {
  const router = useRouter();
  const [kindFilter, setKindFilter] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<string>('open');

  const { data: artifacts = [], isLoading, refetch } = useArtifactsList(
    undefined,
    kindFilter || undefined,
    statusFilter
  );
  const dismissMutation = useDismissArtifact();

  const handleDismiss = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation();
    if (!confirm(`Dismiss artifact #${id}? It will be marked expired.`)) return;
    await dismissMutation.mutateAsync(id);
  };

  const columns: Column<ArtifactSummary>[] = [
    { header: 'ID', accessorKey: 'id', className: 'w-12 font-mono text-xs text-muted-foreground' },
    {
      header: 'Kind',
      cell: (a) => (
        <span className="rounded bg-secondary/80 px-2 py-0.5 text-xs uppercase font-medium">
          {a.kind}
        </span>
      ),
    },
    {
      header: 'Title',
      cell: (a) => (
        <div>
          <span className="font-semibold text-foreground">{a.title}</span>
          {a.thread_id && (
            <div className="font-mono text-[10px] text-muted-foreground">
              thread: {a.thread_id.slice(0, 12)}...
            </div>
          )}
        </div>
      ),
    },
    {
      header: 'Created',
      cell: (a) => (
        <span className="font-mono text-xs text-muted-foreground">
          {new Date(a.created_at).toLocaleDateString()}
        </span>
      ),
    },
    {
      header: 'Status',
      cell: (a) => (
        <span
          className={`rounded px-2 py-0.5 text-[11px] font-medium capitalize ${
            a.status === 'open'
              ? 'bg-emerald-500/20 text-emerald-300'
              : 'bg-muted text-muted-foreground'
          }`}
        >
          {a.status}
        </span>
      ),
    },
    {
      header: '',
      align: 'right',
      cell: (a) => (
        <div className="flex items-center justify-end gap-2">
          {a.status === 'open' && (
            <button
              onClick={(e) => handleDismiss(e, a.id)}
              className="p-1 text-muted-foreground hover:text-rose-400 transition-colors"
              title="Dismiss artifact"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          )}
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        </div>
      ),
    },
  ];

  return (
    <PageLayout
      title="Analysis Artifacts"
      description="Inspect durable query artifacts, re-materialize cached rows, and drill down"
      breadcrumbs={[{ label: 'Artifacts' }]}
    >
      <div className="space-y-4 max-w-5xl">
        <GlassCard className="flex items-center justify-between p-4">
          <div className="flex items-center gap-3">
            <Filter className="h-4 w-4 text-muted-foreground" />
            <select
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value)}
              className="rounded-md border border-border bg-secondary/40 px-3 py-1.5 text-xs text-foreground"
            >
              <option value="">All Artifact Kinds</option>
              <option value="transaction_list">Transaction List</option>
              <option value="group_summary">Group Summary</option>
              <option value="total">Total</option>
              <option value="value_list">Value List</option>
              <option value="mapping_preview">Mapping Preview</option>
              <option value="comparison">Comparison</option>
              <option value="large_tool_output">Large Output</option>
            </select>

            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded-md border border-border bg-secondary/40 px-3 py-1.5 text-xs text-foreground"
            >
              <option value="open">Open Artifacts</option>
              <option value="expired">Expired / Dismissed</option>
            </select>
          </div>
          <span className="text-xs text-muted-foreground">
            {artifacts.length} artifacts found
          </span>
        </GlassCard>

        <DataTable
          data={artifacts}
          columns={columns}
          keyExtractor={(a) => a.id}
          onRowClick={(a) => router.push(`/artifacts/${a.id}`)}
          emptyMessage={isLoading ? 'Loading artifacts...' : 'No artifacts found.'}
        />
      </div>
    </PageLayout>
  );
}
