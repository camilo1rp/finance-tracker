import { describe, expect, it, beforeAll, afterAll, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { server } from './mocks/server';
import { sanitizeFilters, sanitizeCategoryFilter, UNASSIGNED_SENTINEL } from '@/api/filters';
import { buildTransactionsDeepLink } from '@/lib/useFilterParams';
import { getArtifactComponent } from '@/components/artifacts/registry';
import { ArtifactFallback } from '@/components/artifacts/ArtifactFallback';
import { StewardApprovalCard } from '@/components/artifacts/StewardApprovalCard';
import { StatCard } from '@/components/ui/StatCard';
import { addMoney, formatMoney, toDecimal } from '@/lib/money';
import Decimal from 'decimal.js';

beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('Contract Traps & Invariant Regression Suite', () => {
  it('TRAP 1: Drill-down NEVER omits date_to (INV-26)', () => {
    // When date_to is omitted from drill-down input, it must default to today
    const deepLink = buildTransactionsDeepLink({
      category: 'Dining',
      date_from: '2026-01-01',
      // date_to omitted
    });

    expect(deepLink).toContain('date_to=');
    expect(deepLink).toContain('category=Dining');
    expect(deepLink).toContain('date_from=2026-01-01');

    const sanitized = sanitizeFilters({ category: 'Dining' });
    expect(sanitized.date_to).toBeDefined();
    expect(sanitized.date_to.length).toBe(10); // YYYY-MM-DD
  });

  it('TRAP 2: Translates (unassigned) sentinel into undefined before category query', () => {
    // Passing (unassigned) as category raises 422 UnknownLabelFilterError
    const category = sanitizeCategoryFilter('(unassigned)');
    expect(category).toBeUndefined();

    const fullFilters = sanitizeFilters({ category: UNASSIGNED_SENTINEL });
    expect(fullFilters.category).toBeUndefined();
  });

  it('TRAP 3: StewardApprovalCard sends 0-based op indices and full op objects, not arbitrary UI IDs', () => {
    const mockOps = [
      { op: 'create', kind: 'merchant', raw_value: 'UBER', canonical_value: 'Uber' },
      { op: 'update', mapping_id: 42, canonical_value: 'Lyft' },
      { op: 'delete', mapping_id: 99 },
    ];

    let approvedPayload: any = null;

    render(
      <StewardApprovalCard
        interrupt={{
          ops: mockOps,
          preview: { ops: [{ index: 0 }, { index: 1 }, { index: 2 }] },
          rationale: 'Clean up rideshare rules',
        }}
        onApprove={(ops) => {
          approvedPayload = ops;
        }}
      />
    );

    const approveButton = screen.getByText(/Approve All/i);
    fireEvent.click(approveButton);

    // Verified: Sends full op objects matching server contract
    expect(approvedPayload).toEqual(mockOps);
    expect(approvedPayload[0].raw_value).toBe('UBER');
    expect(approvedPayload[1].mapping_id).toBe(42);
  });

  it('TRAP 4: Money amounts are never coerced through Number or parseFloat', () => {
    // Test known IEEE 754 precision trap
    const a = '0.10';
    const b = '0.20';
    const sum = addMoney(a, b);
    expect(sum).toBe('0.30'); // Not 0.30000000000000004!

    const parsed = toDecimal('12345678901234.56');
    expect(parsed instanceof Decimal).toBe(true);
    expect(parsed.toFixed(2)).toBe('12345678901234.56');
  });

  it('TRAP 5: Registry maps all 7 server-side artifact kinds and safely falls back on unknown', () => {
    const kinds = [
      'transaction_list',
      'group_summary',
      'total',
      'value_list',
      'mapping_preview',
      'comparison',
      'large_tool_output',
    ];

    for (const kind of kinds) {
      const comp = getArtifactComponent(kind);
      expect(comp).toBeDefined();
      expect(comp).not.toBe(ArtifactFallback);
    }

    expect(getArtifactComponent('nonexistent_kind')).toBe(ArtifactFallback);
  });

  it('TRAP 6: StatCard renders spend (purchases - refunds) and SPEND (purchases only) with distinct labels', () => {
    render(
      <StatCard
        title="Category Total"
        spend="150.00"
        purchases="170.00"
        refunds="20.00"
        count={5}
      />
    );

    expect(screen.getByText('Spend (Purchases − Refunds)')).toBeDefined();
    expect(screen.getByText(/Purchases \(SPEND\):/i)).toBeDefined();
    expect(screen.getByText(/Refunds:/i)).toBeDefined();
  });

  it('TRAP 7: Total artifact renderer resolves spend and cash flow from data, digest, or text without displaying $0.00 fallback', () => {
    const TotalComponent = getArtifactComponent('total');

    // Case 1: Legacy text-only digest
    const { unmount: unmount1 } = render(
      <TotalComponent
        artifact={{
          id: 18,
          kind: 'total',
          title: 'Totals breakdown',
          digest: {
            text: 'Artifact #18 — "Totals breakdown"\nkind=total rows=0 match_count=unknown truncated=false\nspend=47429.36 net_cash_flow=26448.07 purchases=48987.81 refunds=1558.45 total=47429.36\nfilters: owner_id=1',
            kind: 'total',
            title: 'Totals breakdown',
            artifact_id: 18,
          },
        }}
      />
    );
    expect(screen.getByText('$47,429.36')).toBeDefined();
    expect(screen.queryByText('$0.00')).toBeNull();
    unmount1();

    // Case 2: Structured digest fields
    const { unmount: unmount2 } = render(
      <TotalComponent
        artifact={{
          id: 19,
          kind: 'total',
          title: 'Totals breakdown',
          digest: {
            spend: '47429.36',
            net_cash_flow: '26448.07',
            purchases: '48987.81',
            refunds: '1558.45',
          },
        }}
      />
    );
    expect(screen.getByText('$47,429.36')).toBeDefined();
    unmount2();

    // Case 3: Cached data payload
    render(
      <TotalComponent
        artifact={{
          id: 20,
          kind: 'total',
          title: 'Totals breakdown',
          data: {
            spend: '47429.36',
            net_cash_flow: '26448.07',
            purchases: '48987.81',
            refunds: '1558.45',
          },
        }}
      />
    );
    expect(screen.getByText('$47,429.36')).toBeDefined();
  });
});
