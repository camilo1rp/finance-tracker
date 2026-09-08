import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { GlassCard } from '@/components/ui/GlassCard';
import { DataTable, Column } from '@/components/ui/DataTable';
import { formatMoney } from '@/lib/money';
import { useArtifactRows } from '@/api/hooks/useArtifacts';
import { buildTransactionsDeepLink } from '@/lib/useFilterParams';
import { AlertCircle, ExternalLink, ArrowUpRight, ChevronRight, Layers } from 'lucide-react';

export interface AgentTransactionTableProps {
  artifact?: {
    id?: number;
    title?: string;
    digest?: any;
    data?: any;
    spec?: any;
  };
  pagedRows?: any[];
  matchCount?: number;
  truncated?: boolean;
  onOpenFull?: (artifactId: number) => void;
}

export function AgentTransactionTable({
  artifact,
  pagedRows,
  matchCount,
  truncated,
  onOpenFull,
}: AgentTransactionTableProps) {
  const router = useRouter();

  // Dynamic server pagination and sorting state
  const [pageIndex, setPageIndex] = useState(0);
  const [pageSize, setPageSize] = useState(10);
  const [sortCol, setSortCol] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc' | null>(null);

  const isServerBacked = Boolean(artifact?.id);
  const sortParam = sortCol && sortDir ? `${sortCol}:${sortDir}` : undefined;

  const {
    data: rowsResponse,
    isFetching: isRowsFetching,
  } = useArtifactRows(
    isServerBacked ? artifact?.id : undefined,
    {
      limit: pageSize,
      offset: pageIndex * pageSize,
      sort: sortParam,
    }
  );

  // Extract transactions from server response, paged rows, or initial digest/cache snapshot
  const digestData = artifact?.digest || {};
  const data = artifact?.data || {};

  const transactions =
    rowsResponse?.transactions ||
    rowsResponse?.rows ||
    pagedRows ||
    data.transactions ||
    digestData.sample_rows ||
    digestData.transactions ||
    [];

  const totalMatches =
    matchCount ??
    rowsResponse?.match_count ??
    data.match_count ??
    digestData.match_count ??
    transactions.length;

  const isTruncated =
    truncated ??
    rowsResponse?.truncated ??
    data.truncated ??
    digestData.truncated ??
    (!pagedRows && totalMatches > transactions.length);

  const handleSortChange = (colKey: string, direction: 'asc' | 'desc' | null) => {
    setSortCol(direction ? colKey : null);
    setSortDir(direction);
    setPageIndex(0);
  };

  const handlePageChange = (newPage: number) => {
    setPageIndex(newPage);
  };

  const handlePageSizeChange = (newSize: number) => {
    setPageSize(newSize);
    setPageIndex(0);
  };

  const handleOpenArtifact = () => {
    if (!artifact?.id) return;
    if (onOpenFull) {
      onOpenFull(artifact.id);
    } else {
      router.push(`/artifacts/${artifact.id}`);
    }
  };

  const handleOpenInLedger = () => {
    const specKw = artifact?.spec?.kwargs || {};
    const link = buildTransactionsDeepLink({
      date_from: typeof specKw.date_from === 'string' ? specKw.date_from : undefined,
      date_to: typeof specKw.date_to === 'string' ? specKw.date_to : undefined,
      account_id: typeof specKw.account_id === 'number' ? specKw.account_id : undefined,
      owner_id: typeof specKw.owner_id === 'number' ? specKw.owner_id : undefined,
      merchant: typeof specKw.merchant === 'string' ? specKw.merchant : undefined,
      category: typeof specKw.category === 'string' ? specKw.category : undefined,
      subcategory: typeof specKw.subcategory === 'string' ? specKw.subcategory : undefined,
    });
    router.push(link);
  };

  const columns: Column<any>[] = [
    {
      header: 'Date',
      accessorKey: 'transaction_date',
      sortKey: 'transaction_date',
      sortable: true,
      className: 'whitespace-nowrap font-mono text-xs',
    },
    {
      header: 'Description',
      accessorKey: 'description',
      sortKey: 'description',
      sortable: true,
      cell: (item) => (
        <div>
          <div className="font-medium text-foreground">{item.description}</div>
          {item.effective_merchant && (
            <div className="text-xs text-muted-foreground">{item.effective_merchant}</div>
          )}
        </div>
      ),
    },
    {
      header: 'Category',
      accessorKey: 'effective_category',
      sortKey: 'category',
      sortable: true,
      cell: (item) => (
        <span className="rounded bg-secondary px-2 py-0.5 text-xs text-secondary-foreground font-medium">
          {item.effective_category || '(unassigned)'}
        </span>
      ),
    },
    {
      header: 'Amount',
      accessorKey: 'amount',
      sortKey: 'amount',
      sortable: true,
      align: 'right',
      cell: (item) => (
        <span className="font-mono font-semibold text-foreground">
          {formatMoney(item.amount)}
        </span>
      ),
    },
    {
      header: '',
      align: 'right',
      className: 'w-6',
      cell: () => <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/60" />,
    },
  ];

  return (
    <GlassCard className="flex flex-col gap-3">
      {/* Header bar with title, live count, and action links */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/30 pb-2">
        <div className="flex items-center gap-2">
          <Layers className="h-4 w-4 text-primary flex-shrink-0" />
          <div>
            <h3 className="font-semibold text-foreground text-sm">
              {artifact?.title || 'Transactions'}
            </h3>
            <p className="text-[11px] text-muted-foreground">
              {totalMatches} matching {totalMatches === 1 ? 'transaction' : 'transactions'}
              {isServerBacked && ' • Dynamic live paging'}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 text-xs">
          {artifact?.spec?.kwargs && (
            <button
              onClick={handleOpenInLedger}
              className="flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors"
              title="Open query in transaction ledger"
            >
              <span>Ledger View</span>
              <ArrowUpRight className="h-3.5 w-3.5" />
            </button>
          )}

          {artifact?.id && (
            <button
              onClick={handleOpenArtifact}
              className="flex items-center gap-1 font-medium text-primary hover:underline"
              title="Open full artifact details"
            >
              <span>Full Artifact #{artifact.id}</span>
              <ExternalLink className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      {/* Truncation warning if not using server backing */}
      {!isServerBacked && isTruncated && (
        <div className="flex items-center gap-2 rounded-md bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
          <AlertCircle className="h-4 w-4 flex-shrink-0" />
          <span>
            Showing sample slice (~{transactions.length} rows of {totalMatches} matches).
          </span>
        </div>
      )}

      {/* Interactive Data Table with Sorting, Paging, and Search */}
      <DataTable
        data={transactions}
        columns={columns}
        keyExtractor={(item, index) => item.id ?? index}
        matchCount={totalMatches}
        truncated={!isServerBacked && isTruncated}
        pagination={true}
        page={isServerBacked ? pageIndex : undefined}
        pageSize={isServerBacked ? pageSize : 10}
        totalCount={isServerBacked ? totalMatches : undefined}
        onPageChange={isServerBacked ? handlePageChange : undefined}
        onPageSizeChange={isServerBacked ? handlePageSizeChange : undefined}
        pageSizeOptions={[10, 25, 50]}
        sortColumn={isServerBacked ? sortCol : undefined}
        sortDirection={isServerBacked ? sortDir : undefined}
        onSortChange={isServerBacked ? handleSortChange : undefined}
        searchable={true}
        searchPlaceholder="Filter transactions in this table..."
        loading={isRowsFetching}
        onRowClick={(item) => {
          if (item?.id) {
            router.push(`/transactions/${item.id}`);
          }
        }}
        emptyMessage="No transactions found."
      />
    </GlassCard>
  );
}
