'use client';

import React, { useState, Suspense } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { PageLayout } from '@/components/ui/PageLayout';
import { GlassCard } from '@/components/ui/GlassCard';
import { StatCard } from '@/components/ui/StatCard';
import {
  useAnalyticsTotal,
  useAnalyticsByCategory,
  useAnalyticsByMonth,
  useAnalyticsTopMerchants,
  useAnalyticsLargest,
  useAnalyticsUnmapped,
} from '@/api/hooks/useAnalytics';
import { useAccountsList } from '@/api/hooks/useAccounts';
import { useOwnersList } from '@/api/hooks/useOwners';
import { useFilterParams, buildTransactionsDeepLink } from '@/lib/useFilterParams';
import { formatMoney, formatSpend } from '@/lib/money';
import {
  Calendar,
  Filter,
  DollarSign,
  TrendingDown,
  AlertCircle,
  ExternalLink,
  ChevronRight,
  BarChart3,
} from 'lucide-react';

function DashboardContent() {
  const router = useRouter();
  const { filters, setFilters } = useFilterParams();
  const { data: accounts = [] } = useAccountsList();
  const { data: owners = [] } = useOwnersList();

  const [dateFrom, setDateFrom] = useState(filters.date_from || '');
  const [dateTo, setDateTo] = useState(filters.date_to);
  const [selectedAccountId, setSelectedAccountId] = useState<number | undefined>(filters.account_id);
  const [selectedOwnerId, setSelectedOwnerId] = useState<number | undefined>(filters.owner_id);

  // Queries
  const { data: totalData } = useAnalyticsTotal(filters);
  const { data: unmappedData } = useAnalyticsUnmapped();
  const { data: byCategoryData } = useAnalyticsByCategory(filters, 8);
  const { data: byMonthData } = useAnalyticsByMonth(filters, 12);
  const { data: topMerchants = [] } = useAnalyticsTopMerchants(filters, 5);
  const { data: largestData } = useAnalyticsLargest(filters, 5);

  const handleFilterSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setFilters({
      date_from: dateFrom.trim() || undefined,
      date_to: dateTo.trim() || filters.date_to, // ALWAYS explicit date_to!
      account_id: selectedAccountId,
      owner_id: selectedOwnerId,
    });
  };

  const navigateDrillDown = (extraFilters: Record<string, any>) => {
    const link = buildTransactionsDeepLink({
      ...filters,
      ...extraFilters,
    });
    router.push(link);
  };

  return (
    <PageLayout
      title="Financial Dashboard"
      description="Holistic overview of cash flows, spending categories, and ledger health"
      breadcrumbs={[{ label: 'Dashboard' }]}
    >
      <div className="space-y-6 max-w-7xl">
        {/* Global Filter Bar */}
        <GlassCard className="p-4">
          <form onSubmit={handleFilterSubmit} className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2">
              <Calendar className="h-4 w-4 text-muted-foreground" />
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                className="rounded-md border border-border bg-secondary/40 px-2 py-1.5 text-xs text-foreground focus:outline-none"
              />
              <span className="text-xs text-muted-foreground">to</span>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                className="rounded-md border border-border bg-secondary/40 px-2 py-1.5 text-xs text-foreground focus:outline-none"
              />
            </div>

            <select
              value={selectedAccountId ?? ''}
              onChange={(e) => setSelectedAccountId(e.target.value ? Number(e.target.value) : undefined)}
              className="rounded-md border border-border bg-secondary/40 px-2 py-1.5 text-xs text-foreground"
            >
              <option value="">All Accounts</option>
              {accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name} (•••• {a.last4})
                </option>
              ))}
            </select>

            <select
              value={selectedOwnerId ?? ''}
              onChange={(e) => setSelectedOwnerId(e.target.value ? Number(e.target.value) : undefined)}
              className="rounded-md border border-border bg-secondary/40 px-2 py-1.5 text-xs text-foreground"
            >
              <option value="">All Owners</option>
              {owners.map((o) => (
                <option key={o.id} value={o.id}>
                  {o.name}
                </option>
              ))}
            </select>

            <button
              type="submit"
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:bg-primary/90"
            >
              <Filter className="h-3.5 w-3.5" />
              <span>Apply Filters</span>
            </button>
          </form>
        </GlassCard>

        {/* Top KPI Tiles */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatCard
            title="Total Spend"
            spend={totalData?.spend}
            purchases={totalData?.purchases}
            refunds={totalData?.refunds}
            count={totalData?.count}
            onClick={() => navigateDrillDown({ transaction_type: 'SPEND' })}
          />

          <StatCard
            title="Net Cash Flow"
            netCashFlow={totalData?.net_cash_flow}
            purchases={totalData?.purchases}
            count={totalData?.count}
            leadWithNetCashFlow
            onClick={() => navigateDrillDown({})}
          />

          <StatCard
            title="Total Purchases (SPEND)"
            spend={totalData?.purchases}
            count={totalData?.count}
            onClick={() => navigateDrillDown({ transaction_type: 'SPEND' })}
          />

          {/* Unmapped Ledger-Wide Tile */}
          <GlassCard
            className="cursor-pointer hover:border-amber-500/50"
            variant="warning"
            interactive
            onClick={() => router.push('/mappings')}
          >
            <div className="flex items-start justify-between">
              <div>
                <p className="text-xs font-medium uppercase tracking-wider text-amber-300">
                  Unmapped Values
                </p>
                <div className="mt-2 flex items-baseline gap-2">
                  <span className="text-2xl font-bold tracking-tight text-amber-200">
                    {(unmappedData?.categories?.length ?? 0) + (unmappedData?.merchants?.length ?? 0)}
                  </span>
                  <span className="text-xs text-muted-foreground">Ledger-wide</span>
                </div>
              </div>
              <AlertCircle className="h-5 w-5 text-amber-400" />
            </div>
            <p className="mt-4 border-t border-amber-600/20 pt-2 text-[10px] text-muted-foreground">
              Ignores active date/account filters. Click to open Rules Workbench.
            </p>
          </GlassCard>
        </div>

        {/* Charts & Categorization Breakdown */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Spend by Category */}
          <GlassCard>
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-foreground flex items-center gap-2">
                <BarChart3 className="h-4 w-4 text-primary" />
                <span>Spend by Category</span>
              </h3>
              {byCategoryData?.truncated && (
                <span className="rounded bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-300">
                  Top 8 shown ({byCategoryData.match_count} total)
                </span>
              )}
            </div>

            <div className="space-y-2">
              {byCategoryData?.groups?.map((cat, idx) => (
                <div
                  key={idx}
                  onClick={() => navigateDrillDown({ category: cat.group_value })}
                  className="flex items-center justify-between p-2 rounded-lg hover:bg-secondary/60 cursor-pointer transition-colors text-xs"
                >
                  <div className="flex flex-col">
                    <span className="font-medium text-foreground">{cat.group_value}</span>
                    <span className="text-[10px] text-muted-foreground">
                      Purchases: {formatSpend(cat.purchases)} | Refunds: {formatMoney(cat.refunds)}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-semibold text-foreground">
                      {formatSpend(cat.spend)}
                    </span>
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                  </div>
                </div>
              ))}
              {(!byCategoryData?.groups || byCategoryData.groups.length === 0) && (
                <div className="text-xs text-muted-foreground py-6 text-center">
                  No categorical spend recorded in this range.
                </div>
              )}
            </div>
          </GlassCard>

          {/* Spend by Month */}
          <GlassCard>
            <div className="flex items-center justify-between mb-3">
              <h3 className="font-semibold text-foreground flex items-center gap-2">
                <Calendar className="h-4 w-4 text-primary" />
                <span>Monthly Spend Cadence</span>
              </h3>
              {byMonthData?.truncated && (
                <span className="rounded bg-amber-500/10 px-2 py-0.5 text-[10px] text-amber-300">
                  Last 12 months ({byMonthData.match_count} total)
                </span>
              )}
            </div>

            <div className="space-y-2">
              {byMonthData?.groups?.map((m, idx) => (
                <div
                  key={idx}
                  onClick={() =>
                    navigateDrillDown({
                      date_from: `${m.group_value}-01`,
                      date_to: `${m.group_value}-31`,
                    })
                  }
                  className="flex items-center justify-between p-2 rounded-lg hover:bg-secondary/60 cursor-pointer transition-colors text-xs"
                >
                  <span className="font-mono font-medium text-foreground">{m.group_value}</span>
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-semibold text-foreground">
                      {formatSpend(m.spend)}
                    </span>
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                  </div>
                </div>
              ))}
            </div>
          </GlassCard>
        </div>

        {/* Top Merchants & Largest Transactions */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Top Merchants */}
          <GlassCard>
            <h3 className="font-semibold text-foreground mb-3">Top Merchants</h3>
            <div className="divide-y divide-border/30 text-xs">
              {topMerchants.map((m, idx) => (
                <div
                  key={idx}
                  onClick={() => navigateDrillDown({ merchant: m.merchant })}
                  className="flex items-center justify-between py-2.5 hover:bg-secondary/40 cursor-pointer transition-colors"
                >
                  <div>
                    <span className="font-medium text-foreground">{m.merchant}</span>
                    <div className="text-[10px] text-muted-foreground">{m.count} transactions</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-semibold text-foreground">{formatSpend(m.spend)}</span>
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                  </div>
                </div>
              ))}
            </div>
          </GlassCard>

          {/* Largest Transactions */}
          <GlassCard>
            <h3 className="font-semibold text-foreground mb-3">Largest Transactions</h3>
            <div className="divide-y divide-border/30 text-xs">
              {largestData?.transactions?.map((t) => (
                <div
                  key={t.id}
                  onClick={() => router.push(`/transactions/${t.id}`)}
                  className="flex items-center justify-between py-2.5 hover:bg-secondary/40 cursor-pointer transition-colors"
                >
                  <div>
                    <span className="font-medium text-foreground">{t.description}</span>
                    <div className="text-[10px] text-muted-foreground">
                      {t.transaction_date} • {t.effective_category || '(unassigned)'}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-bold text-foreground">{formatMoney(t.amount)}</span>
                    <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                  </div>
                </div>
              ))}
            </div>
          </GlassCard>
        </div>
      </div>
    </PageLayout>
  );
}

export default function DashboardPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">
          Loading dashboard metrics...
        </div>
      }
    >
      <DashboardContent />
    </Suspense>
  );
}
