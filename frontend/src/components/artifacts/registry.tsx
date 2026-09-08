import React from 'react';
import { AgentTransactionTable } from './AgentTransactionTable';
import { AgentCategoryChart } from './AgentCategoryChart';
import { StatCard } from '@/components/ui/StatCard';
import { CatalogList } from './CatalogList';
import { StewardApprovalCard } from './StewardApprovalCard';
import { ComparisonView } from './ComparisonView';
import { CollapsedRawViewer } from './CollapsedRawViewer';
import { ArtifactFallback } from './ArtifactFallback';

export type ArtifactKind =
  | 'transaction_list'
  | 'group_summary'
  | 'total'
  | 'value_list'
  | 'mapping_preview'
  | 'comparison'
  | 'large_tool_output';

function parseKeyFromText(text: any, key: string): string | undefined {
  if (typeof text !== 'string') return undefined;
  const match = text.match(new RegExp(`(?:^|\\s)${key}=([0-9.-]+)`));
  return match ? match[1] : undefined;
}

export const ARTIFACT_REGISTRY: Record<string, React.ComponentType<any>> = {
  transaction_list: AgentTransactionTable,
  group_summary: AgentCategoryChart,
  total: (props: any) => {
    const data = props.artifact?.data || props.data || {};
    const digest = props.artifact?.digest || {};
    const digestText = typeof digest === 'object' ? digest.text : String(digest || '');

    const spend =
      data.spend ??
      digest.spend ??
      data.total ??
      digest.total ??
      parseKeyFromText(digestText, 'spend') ??
      parseKeyFromText(digestText, 'total');
    const netCashFlow =
      data.net_cash_flow ??
      digest.net_cash_flow ??
      parseKeyFromText(digestText, 'net_cash_flow');
    const purchases =
      data.purchases ??
      digest.purchases ??
      parseKeyFromText(digestText, 'purchases');
    const refunds =
      data.refunds ??
      digest.refunds ??
      parseKeyFromText(digestText, 'refunds');
    const count =
      data.count ??
      digest.count ??
      (parseKeyFromText(digestText, 'count') ? Number(parseKeyFromText(digestText, 'count')) : undefined);
    const matchCount =
      props.matchCount ??
      data.match_count ??
      digest.match_count ??
      (parseKeyFromText(digestText, 'match_count') ? Number(parseKeyFromText(digestText, 'match_count')) : undefined);
    const truncated =
      props.truncated ??
      data.truncated ??
      digest.truncated ??
      (typeof digestText === 'string' && digestText.includes('truncated=true'));

    return (
      <StatCard
        title={props.artifact?.title || 'Totals'}
        spend={spend}
        netCashFlow={netCashFlow}
        purchases={purchases}
        refunds={refunds}
        count={count}
        matchCount={matchCount}
        truncated={truncated}
        {...props}
      />
    );
  },
  value_list: CatalogList,
  mapping_preview: StewardApprovalCard,
  comparison: ComparisonView,
  large_tool_output: CollapsedRawViewer,
};

export function getArtifactComponent(kind: string): React.ComponentType<any> {
  return ARTIFACT_REGISTRY[kind] || ArtifactFallback;
}

export interface ArtifactRendererProps {
  artifact: {
    id?: number;
    kind: string;
    title?: string;
    spec?: any;
    digest?: any;
    data?: any;
  };
  onOpen?: (id: number) => void;
  [key: string]: any;
}

export function ArtifactRenderer({ artifact, onOpen, ...rest }: ArtifactRendererProps) {
  const Component = getArtifactComponent(artifact?.kind);
  return <Component artifact={artifact} onOpen={onOpen} {...rest} />;
}
