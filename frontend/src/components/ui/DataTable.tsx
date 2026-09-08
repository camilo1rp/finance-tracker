import React, { useState, useMemo } from 'react';
import { cn } from '@/lib/utils';
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Search,
  X,
  Loader2,
} from 'lucide-react';

export interface Column<T> {
  header: string;
  accessorKey?: keyof T;
  cell?: (item: T) => React.ReactNode;
  align?: 'left' | 'center' | 'right';
  className?: string;
  sortable?: boolean;
  sortKey?: string;
}

export interface DataTableProps<T> {
  data: T[];
  columns: Column<T>[];
  keyExtractor: (item: T, index: number) => string | number;
  matchCount?: number;
  truncated?: boolean;
  emptyMessage?: string;
  onRowClick?: (item: T) => void;
  className?: string;

  // Sorting
  sortColumn?: string | null;
  sortDirection?: 'asc' | 'desc' | null;
  onSortChange?: (columnKey: string, direction: 'asc' | 'desc' | null) => void;

  // In-table search / quick filter
  searchable?: boolean;
  searchPlaceholder?: string;
  searchValue?: string;
  onSearchChange?: (val: string) => void;

  // Pagination
  pagination?: boolean;
  page?: number;            // 0-indexed
  pageSize?: number;
  totalCount?: number;
  onPageChange?: (newPage: number) => void;
  onPageSizeChange?: (newSize: number) => void;
  pageSizeOptions?: number[];

  // Loading state
  loading?: boolean;
}

function compareValues(a: any, b: any, direction: 'asc' | 'desc') {
  if (a == null && b == null) return 0;
  if (a == null) return direction === 'asc' ? 1 : -1;
  if (b == null) return direction === 'asc' ? -1 : 1;

  // Numbers (including decimal strings)
  const numA = typeof a === 'number' ? a : !isNaN(Number(a)) && a !== '' ? Number(a) : null;
  const numB = typeof b === 'number' ? b : !isNaN(Number(b)) && b !== '' ? Number(b) : null;
  if (numA !== null && numB !== null) {
    return direction === 'asc' ? numA - numB : numB - numA;
  }

  // Dates
  const dateA = Date.parse(a);
  const dateB = Date.parse(b);
  if (!isNaN(dateA) && !isNaN(dateB) && typeof a === 'string' && a.includes('-')) {
    return direction === 'asc' ? dateA - dateB : dateB - dateA;
  }

  const strA = String(a).toLowerCase();
  const strB = String(b).toLowerCase();
  return direction === 'asc' ? strA.localeCompare(strB) : strB.localeCompare(strA);
}

export function DataTable<T>({
  data,
  columns,
  keyExtractor,
  matchCount,
  truncated,
  emptyMessage = 'No records found.',
  onRowClick,
  className,
  sortColumn: controlledSortColumn,
  sortDirection: controlledSortDirection,
  onSortChange,
  searchable = false,
  searchPlaceholder = 'Quick search rows...',
  searchValue: controlledSearchValue,
  onSearchChange,
  pagination = false,
  page: controlledPage,
  pageSize: controlledPageSize,
  totalCount,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 25, 50],
  loading = false,
}: DataTableProps<T>) {
  // Client state fallbacks
  const [localSearch, setLocalSearch] = useState('');
  const [localSortCol, setLocalSortCol] = useState<string | null>(null);
  const [localSortDir, setLocalSortDir] = useState<'asc' | 'desc' | null>(null);
  const [localPage, setLocalPage] = useState(0);
  const [localPageSize, setLocalPageSize] = useState(controlledPageSize || 10);

  const activeSearch = controlledSearchValue !== undefined ? controlledSearchValue : localSearch;
  const activeSortCol = controlledSortColumn !== undefined ? controlledSortColumn : localSortCol;
  const activeSortDir = controlledSortDirection !== undefined ? controlledSortDirection : localSortDir;
  const isServerPagination = onPageChange !== undefined;
  const activePage = controlledPage !== undefined ? controlledPage : localPage;
  const activePageSize = controlledPageSize !== undefined ? controlledPageSize : localPageSize;

  const handleSearchChange = (val: string) => {
    if (onSearchChange) {
      onSearchChange(val);
    } else {
      setLocalSearch(val);
      setLocalPage(0); // Reset page on filter
    }
  };

  const handleSortClick = (colKey: string) => {
    let nextDir: 'asc' | 'desc' | null = 'asc';
    if (activeSortCol === colKey) {
      if (activeSortDir === 'asc') nextDir = 'desc';
      else if (activeSortDir === 'desc') nextDir = null;
      else nextDir = 'asc';
    }

    if (onSortChange) {
      onSortChange(colKey, nextDir);
    } else {
      setLocalSortCol(nextDir ? colKey : null);
      setLocalSortDir(nextDir);
      setLocalPage(0);
    }
  };

  const handlePageChange = (newPage: number) => {
    if (onPageChange) {
      onPageChange(newPage);
    } else {
      setLocalPage(newPage);
    }
  };

  const handlePageSizeChange = (newSize: number) => {
    if (onPageSizeChange) {
      onPageSizeChange(newSize);
    } else {
      setLocalPageSize(newSize);
      setLocalPage(0);
    }
  };

  // Client-side filtering
  const filteredData = useMemo(() => {
    if (onSearchChange || !activeSearch.trim()) {
      return data;
    }
    const q = activeSearch.toLowerCase().trim();
    return data.filter((item: any) => {
      for (const col of columns) {
        if (col.accessorKey && item[col.accessorKey] != null) {
          if (String(item[col.accessorKey]).toLowerCase().includes(q)) return true;
        }
      }
      return false;
    });
  }, [data, activeSearch, onSearchChange, columns]);

  // Client-side sorting
  const sortedData = useMemo(() => {
    if (onSortChange || !activeSortCol || !activeSortDir) {
      return filteredData;
    }
    return [...filteredData].sort((itemA: any, itemB: any) => {
      const valA = itemA[activeSortCol];
      const valB = itemB[activeSortCol];
      return compareValues(valA, valB, activeSortDir);
    });
  }, [filteredData, activeSortCol, activeSortDir, onSortChange]);

  // Client-side pagination slice
  const pagedData = useMemo(() => {
    if (!pagination || isServerPagination) {
      return sortedData;
    }
    const start = activePage * activePageSize;
    return sortedData.slice(start, start + activePageSize);
  }, [sortedData, pagination, isServerPagination, activePage, activePageSize]);

  const effectiveTotalCount = isServerPagination
    ? (totalCount ?? matchCount ?? data.length)
    : sortedData.length;

  const totalPages = Math.max(1, Math.ceil(effectiveTotalCount / activePageSize));
  const startRowIndex = effectiveTotalCount === 0 ? 0 : activePage * activePageSize + 1;
  const endRowIndex = Math.min(startRowIndex + pagedData.length - 1, effectiveTotalCount);

  return (
    <div className={cn('w-full overflow-hidden rounded-xl border border-border/60 bg-card/40', className)}>
      {/* Top Bar: Search and Status */}
      {searchable && (
        <div className="flex items-center justify-between gap-3 border-b border-border/40 bg-secondary/20 px-3 py-2">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              type="text"
              value={activeSearch}
              onChange={(e) => handleSearchChange(e.target.value)}
              placeholder={searchPlaceholder}
              className="w-full rounded-md border border-border/60 bg-background/60 py-1 pl-8 pr-7 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
            {activeSearch && (
              <button
                onClick={() => handleSearchChange('')}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          {loading && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
              <span className="hidden sm:inline">Updating...</span>
            </div>
          )}
        </div>
      )}

      {truncated && (
        <div className="flex items-center gap-2 border-b border-border/40 bg-amber-500/10 px-4 py-2 text-xs text-amber-300">
          <AlertCircle className="h-4 w-4 flex-shrink-0" />
          <span>
            Partial set displayed ({data.length} rows returned, {matchCount} total matches).
          </span>
        </div>
      )}

      {/* Table */}
      <div className="relative overflow-x-auto">
        {loading && !searchable && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-card/60 backdrop-blur-[1px]">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
          </div>
        )}
        <table className="w-full text-left text-sm">
          <thead className="border-b border-border/60 bg-secondary/50 text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              {columns.map((col, idx) => {
                const colKey = col.sortKey || (col.accessorKey as string);
                const isSortable = col.sortable ?? Boolean(colKey);
                const isSorted = activeSortCol === colKey;

                return (
                  <th
                    key={idx}
                    onClick={() => isSortable && colKey && handleSortClick(colKey)}
                    className={cn(
                      'px-4 py-3 font-medium select-none',
                      col.align === 'right' && 'text-right',
                      col.align === 'center' && 'text-center',
                      isSortable && 'cursor-pointer hover:bg-secondary/70 transition-colors',
                      col.className
                    )}
                  >
                    <div
                      className={cn(
                        'flex items-center gap-1.5',
                        col.align === 'right' && 'justify-end',
                        col.align === 'center' && 'justify-center'
                      )}
                    >
                      <span>{col.header}</span>
                      {isSortable && (
                        <span className="text-muted-foreground">
                          {isSorted && activeSortDir === 'asc' ? (
                            <ArrowUp className="h-3 w-3 text-primary" />
                          ) : isSorted && activeSortDir === 'desc' ? (
                            <ArrowDown className="h-3 w-3 text-primary" />
                          ) : (
                            <ArrowUpDown className="h-3 w-3 opacity-40 hover:opacity-100 transition-opacity" />
                          )}
                        </span>
                      )}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-border/30">
            {pagedData.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="px-4 py-8 text-center text-sm text-muted-foreground">
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              pagedData.map((item, rowIdx) => (
                <tr
                  key={keyExtractor(item, rowIdx)}
                  onClick={() => onRowClick && onRowClick(item)}
                  className={cn(
                    'transition-colors hover:bg-muted/40',
                    onRowClick && 'cursor-pointer'
                  )}
                >
                  {columns.map((col, colIdx) => (
                    <td
                      key={colIdx}
                      className={cn(
                        'px-4 py-3',
                        col.align === 'right' && 'text-right',
                        col.align === 'center' && 'text-center',
                        col.className
                      )}
                    >
                      {col.cell ? col.cell(item) : col.accessorKey ? String(item[col.accessorKey] ?? '') : ''}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination Footer */}
      {pagination && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-border/40 bg-secondary/20 px-4 py-2.5 text-xs text-muted-foreground">
          <div className="flex items-center gap-3">
            <span>
              Showing <span className="font-medium text-foreground">{startRowIndex}</span> to{' '}
              <span className="font-medium text-foreground">{endRowIndex}</span> of{' '}
              <span className="font-medium text-foreground">{effectiveTotalCount}</span>
            </span>

            {pageSizeOptions.length > 1 && (
              <div className="flex items-center gap-1.5 pl-2 border-l border-border/40">
                <span>Per page:</span>
                <select
                  value={activePageSize}
                  onChange={(e) => handlePageSizeChange(Number(e.target.value))}
                  className="rounded border border-border/60 bg-background/70 px-1.5 py-0.5 text-xs text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  {pageSizeOptions.map((opt) => (
                    <option key={opt} value={opt}>
                      {opt}
                    </option>
                  ))}
                </select>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
            <span>
              Page <span className="font-medium text-foreground">{activePage + 1}</span> of{' '}
              <span className="font-medium text-foreground">{totalPages}</span>
            </span>
            <div className="flex items-center gap-1">
              <button
                onClick={() => handlePageChange(Math.max(0, activePage - 1))}
                disabled={activePage <= 0 || loading}
                className="flex h-7 w-7 items-center justify-center rounded border border-border/60 bg-background/60 hover:bg-muted disabled:opacity-40 transition-opacity"
                title="Previous page"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <button
                onClick={() => handlePageChange(Math.min(totalPages - 1, activePage + 1))}
                disabled={activePage >= totalPages - 1 || loading}
                className="flex h-7 w-7 items-center justify-center rounded border border-border/60 bg-background/60 hover:bg-muted disabled:opacity-40 transition-opacity"
                title="Next page"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
