'use client';

import React, { useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { PageLayout } from '@/components/ui/PageLayout';
import { GlassCard } from '@/components/ui/GlassCard';
import { useArtifact, useArtifactRows, useDismissArtifact } from '@/api/hooks/useArtifacts';
import { ArtifactRenderer } from '@/components/artifacts/registry';
import { buildTransactionsDeepLink } from '@/lib/useFilterParams';
import { ArrowLeft, RefreshCw, Trash2, Calendar, Layers } from 'lucide-react';

export default function ArtifactDetailPage() {
  const params = useParams();
  const router = useRouter();
  const artifactId = Number(params.id);

  const [forceRefresh, setForceRefresh] = useState(false);
  const [pageOffset, setPageOffset] = useState(0);
  const limit = 50;

  const { data: artifact, isLoading: artifactLoading, error: artifactError } = useArtifact(artifactId);
  const {
    data: rowsData,
    isLoading: rowsLoading,
    refetch: refetchRows,
  } = useArtifactRows(artifactId, {
    limit,
    offset: pageOffset,
    force_refresh: forceRefresh,
  });

  const dismissMutation = useDismissArtifact();

  const handleForceRefresh = async () => {
    setForceRefresh(true);
    await refetchRows();
    setForceRefresh(false);
  };

  const handleDismiss = async () => {
    if (!confirm(`Dismiss artifact #${artifactId}? It will be marked expired.`)) return;
    await dismissMutation.mutateAsync(artifactId);
    router.push('/artifacts');
  };

  if (artifactLoading) {
    return (
      <PageLayout title="Artifact Viewer" breadcrumbs={[{ label: 'Artifacts', href: '/artifacts' }, { label: '...' }]}>
        <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">
          Loading artifact...
        </div>
      </PageLayout>
    );
  }

  if (artifactError || !artifact) {
    return (
      <PageLayout title="Artifact Viewer" breadcrumbs={[{ label: 'Artifacts', href: '/artifacts' }, { label: 'Error' }]}>
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-xs text-rose-300">
          Failed to load artifact #{artifactId}.
        </div>
      </PageLayout>
    );
  }

  // Drill down from group summary to transactions
  const handleDrillDownCategory = (category: string) => {
    const spec = (artifact.spec as Record<string, any>) || {};
    const link = buildTransactionsDeepLink({
      category,
      date_from: typeof spec.date_from === 'string' ? spec.date_from : undefined,
      date_to: typeof spec.date_to === 'string' ? spec.date_to : undefined,
      account_id: typeof spec.account_id === 'number' ? spec.account_id : undefined,
      owner_id: typeof spec.owner_id === 'number' ? spec.owner_id : undefined,
    });
    router.push(link);
  };

  return (
    <PageLayout
      title={artifact.title}
      description={`Kind: ${artifact.kind} | Status: ${artifact.status} | Created: ${new Date(artifact.created_at).toLocaleString()}`}
      breadcrumbs={[
        { label: 'Artifacts', href: '/artifacts' },
        { label: `#${artifact.id}` },
      ]}
      actions={
        <div className="flex items-center gap-2">
          <button
            onClick={handleForceRefresh}
            disabled={rowsLoading}
            className="flex items-center gap-1.5 rounded-lg border border-border bg-secondary/50 px-3 py-1.5 text-xs text-foreground hover:bg-secondary disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${rowsLoading ? 'animate-spin' : ''}`} />
            <span>Force Re-materialize</span>
          </button>
          {artifact.status === 'open' && (
            <button
              onClick={handleDismiss}
              disabled={dismissMutation.isPending}
              className="flex items-center gap-1.5 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-1.5 text-xs text-rose-300 hover:bg-rose-500/20"
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span>Dismiss</span>
            </button>
          )}
        </div>
      }
    >
      <div className="space-y-6 max-w-5xl">
        {/* Full GenUI Artifact Rendered via Registry */}
        <ArtifactRenderer
          artifact={{
            id: artifact.id,
            kind: artifact.kind,
            title: artifact.title,
            digest: artifact.digest,
            spec: artifact.spec,
          }}
          pagedRows={rowsData?.rows || rowsData?.transactions}
          matchCount={rowsData?.match_count ?? (artifact.digest as any)?.match_count}
          truncated={rowsData?.truncated ?? (artifact.digest as any)?.truncated}
        />

        {/* Query Spec / Parameters Callout */}
        {artifact.spec && (
          <GlassCard variant="subtle" className="text-xs space-y-2">
            <span className="font-semibold text-foreground flex items-center gap-1.5">
              <Calendar className="h-4 w-4 text-primary" />
              <span>Query Specification & Filter Parameters</span>
            </span>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1 font-mono text-[11px]">
              <div>
                <span className="text-muted-foreground block">date_from:</span>
                <span>{String((artifact.spec as any).date_from || '(open)')}</span>
              </div>
              <div>
                <span className="text-muted-foreground block">date_to:</span>
                <span>{String((artifact.spec as any).date_to || '(today)')}</span>
              </div>
              <div>
                <span className="text-muted-foreground block">account_id:</span>
                <span>{String((artifact.spec as any).account_id ?? '(all)')}</span>
              </div>
              <div>
                <span className="text-muted-foreground block">owner_id:</span>
                <span>{String((artifact.spec as any).owner_id ?? '(all)')}</span>
              </div>
            </div>
          </GlassCard>
        )}
      </div>
    </PageLayout>
  );
}
