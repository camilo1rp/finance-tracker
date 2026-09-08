'use client';

import React, { useState, Suspense } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { PageLayout } from '@/components/ui/PageLayout';
import { GlassCard } from '@/components/ui/GlassCard';
import { DataTable, Column } from '@/components/ui/DataTable';
import { useTransactionsList } from '@/api/hooks/useTransactions';
import { useAccountsList } from '@/api/hooks/useAccounts';
import { useOwnersList } from '@/api/hooks/useOwners';
import { TransactionCard } from '@/api/generated/model';
import { useFilterParams } from '@/lib/useFilterParams';
import { formatMoney } from '@/lib/money';
import { Filter, Search, Calendar, ChevronRight, AlertCircle, RefreshCw } from 'lucide-react';

function TransactionsContent() {
  const router = useRouter();
  const { filters, setFilters } = useFilterParams();
  const { data: accounts = [] } = useAccountsList();
  const { data: owners = [] } = useOwnersList();

  const [searchInput, setSearchInput] = useState(filters.merchant || '');
  const [dateFromInput, setDateFromInput] = useState(filters.date_from || '');
  const [dateToInput, setDateToInput] = useState(filters.date_to);

  const { data: pageData, isLoading, error, refetch } = useTransactionsList(filters, 25);

  const handleApplyFilters = (e: React.FormEvent) => {
    e.preventDefault();
    setFilters({
      ...filters,
      merchant: searchInput.trim() || undefined,
      date_from: dateFromInput.trim() || undefined,
      date_to: dateToInput.trim() || filters.date_to, // ALWAYS explicit date_to!
    });
  };

  const is422UnknownLabel =
    error && (error as any).response?.status === 422;

  const columns: Column<TransactionCard>[] = [
    {
      header: 'ID',
      accessorKey: 'id',
      className: 'w-12 font-mono text-xs text-muted-foreground',
    },
    {
      header: 'Date',
      accessorKey: 'transaction_date',
      className: 'whitespace-nowrap font-mono text-xs',
    },
    {
      header: 'Description',
      cell: (t) => (
        <div>
          <div className="font-medium text-foreground">{t.description}</div>
          {t.effective_merchant && (
            <div className="text-xs text-muted-foreground">{t.effective_merchant}</div>
          )}
        </div>
      ),
    },
    {
      header: 'Category / Subcategory',
      cell: (t) => (
        <div className="flex flex-col gap-0.5">
          <span className="rounded bg-secondary/80 px-2 py-0.5 text-xs text-secondary-foreground font-medium w-fit">
            {t.effective_category || '(unassigned)'}
          </span>
          {t.subcategory && (
            <span className="text-[10px] text-muted-foreground ml-1">
              sub: {t.subcategory}
            </span>
          )}
        </div>
      ),
    },
    {
      header: 'Account / Owner',
      cell: (t) => {
        const account = accounts.find((a) => a.id === t.account_id);
        const owner = owners.find((o) => o.id === t.owner_id);
        return (
          <div className="text-xs">
            <span className="text-foreground">{account?.name || `Acct #${t.account_id}`}</span>
            {owner && <span className="text-muted-foreground ml-1">({owner.name})</span>}
          </div>
        );
      },
    },
    {
      header: 'Type',
      cell: (t) => (
        <span className="text-xs uppercase font-mono text-muted-foreground">
          {t.effective_type}
        </span>
      ),
    },
    {
      header: 'Amount',
      align: 'right',
      cell: (t) => (
        <span className="font-mono font-semibold text-foreground">
          {formatMoney(t.amount)}
        </span>
      ),
    },
    {
      header: '',
      align: 'right',
      className: 'w-8',
      cell: () => <ChevronRight className="h-4 w-4 text-muted-foreground" />,
    },
  ];

  return (
    <PageLayout
      title="Transaction Ledger"
      description="Inspect transaction cards, filter ranges, and navigate into triples detail view"
      breadcrumbs={[{ label: 'Transactions' }]}
    >
      <div className="space-y-4">
        {/* Filter bar */}
        <GlassCard className="p-4">
          <form onSubmit={handleApplyFilters} className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 flex-1 min-w-[220px]">
              <Search className="h-4 w-4 text-muted-foreground" />
              <input
                type="text"
                placeholder="Merchant name (exact, or use % for wildcard)..."
                value={searchInput}
                onChange={(e) => setSearchInput(e.target.value)}
                className="w-full rounded-md border border-border bg-secondary/40 px-3 py-1.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>

            <div className="flex items-center gap-2">
              <Calendar className="h-4 w-4 text-muted-foreground" />
              <input
                type="date"
                value={dateFromInput}
                onChange={(e) => setDateFromInput(e.target.value)}
                className="rounded-md border border-border bg-secondary/40 px-2 py-1.5 text-xs text-foreground focus:outline-none"
              />
              <span className="text-xs text-muted-foreground">to</span>
              <input
                type="date"
                value={dateToInput}
                onChange={(e) => setDateToInput(e.target.value)}
                className="rounded-md border border-border bg-secondary/40 px-2 py-1.5 text-xs text-foreground focus:outline-none"
              />
            </div>

            <button
              type="submit"
              className="flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-semibold text-primary-foreground hover:bg-primary/90"
            >
              <Filter className="h-3.5 w-3.5" />
              <span>Apply Filters</span>
            </button>
          </form>
          <p className="mt-2 text-[11px] text-muted-foreground">
            <strong>Merchant filter note:</strong> Searches are exact match unless containing '%' (e.g. 'Coffee%' matches 'Coffee Roasters'). Deep links always carry an explicit date_to.
          </p>
        </GlassCard>

        {/* 422 Unknown Label Error handling */}
        {is422UnknownLabel && (
          <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-xs text-rose-300 flex items-center gap-2">
            <AlertCircle className="h-4 w-4 flex-shrink-0" />
            <span>
              Label Error: No such label exists in this ledger. Category/subcategory filters must match stored records.
            </span>
          </div>
        )}

        {/* Transactions Table */}
        <DataTable
          data={pageData?.transactions || []}
          columns={columns}
          keyExtractor={(t) => t.id}
          matchCount={pageData?.match_count}
          truncated={pageData?.truncated}
          onRowClick={(t) => router.push(`/transactions/${t.id}`)}
          emptyMessage={isLoading ? 'Loading transactions...' : 'No transactions match current filters.'}
        />
      </div>
    </PageLayout>
  );
}

export default function TransactionsPage() {
  return (
    <Suspense
      fallback={
        <div className="flex h-32 items-center justify-center text-xs text-muted-foreground">
          Loading transaction ledger...
        </div>
      }
    >
      <TransactionsContent />
    </Suspense>
  );
}
