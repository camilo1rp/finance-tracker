import { describe, expect, it } from 'vitest';
import {
  ARTIFACT_REGISTRY,
  getArtifactComponent,
} from './registry';
import { AgentTransactionTable } from './AgentTransactionTable';
import { AgentCategoryChart } from './AgentCategoryChart';
import { CatalogList } from './CatalogList';
import { StewardApprovalCard } from './StewardApprovalCard';
import { ComparisonView } from './ComparisonView';
import { CollapsedRawViewer } from './CollapsedRawViewer';
import { ArtifactFallback } from './ArtifactFallback';

describe('Artifact Kind Registry', () => {
  const expectedKinds = [
    'transaction_list',
    'group_summary',
    'total',
    'value_list',
    'mapping_preview',
    'comparison',
    'large_tool_output',
  ];

  it('maps all seven server-side artifact kinds', () => {
    expectedKinds.forEach((kind) => {
      const component = getArtifactComponent(kind);
      expect(component).toBeDefined();
      expect(component).not.toBe(ArtifactFallback);
    });

    expect(getArtifactComponent('transaction_list')).toBe(AgentTransactionTable);
    expect(getArtifactComponent('group_summary')).toBe(AgentCategoryChart);
    expect(getArtifactComponent('value_list')).toBe(CatalogList);
    expect(getArtifactComponent('mapping_preview')).toBe(StewardApprovalCard);
    expect(getArtifactComponent('comparison')).toBe(ComparisonView);
    expect(getArtifactComponent('large_tool_output')).toBe(CollapsedRawViewer);
  });

  it('safely falls back for unknown artifact kinds without throwing', () => {
    expect(getArtifactComponent('unknown_future_kind')).toBe(ArtifactFallback);
    expect(getArtifactComponent('')).toBe(ArtifactFallback);
    expect(getArtifactComponent('insight_card')).toBe(ArtifactFallback);
  });
});
