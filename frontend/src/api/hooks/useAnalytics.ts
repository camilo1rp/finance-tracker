import { useQuery } from '@tanstack/react-query';
import {
  totalAnalyticsTotalGet,
  summaryAnalyticsSummaryGet,
  byCategoryAnalyticsByCategoryGet,
  byMonthAnalyticsByMonthGet,
  byOwnerAnalyticsByOwnerGet,
  bySubcategoryAnalyticsBySubcategoryGet,
  topMerchantsAnalyticsTopMerchantsGet,
  largestAnalyticsLargestGet,
  searchAnalyticsSearchGet,
  cashFlowAnalyticsCashFlowGet,
  valuesAnalyticsValuesGet,
  unmappedValuesAnalyticsUnmappedGet,
} from '@/api/generated/analytics/analytics';
import {
  TotalOut,
  GroupSummaryPage,
  MerchantSummary,
  TransactionListOut,
  CashFlowOut,
  UnmappedValuesOut,
  ValueListOut,
} from '@/api/generated/model';
import { FilterInput, sanitizeFilters } from '@/api/filters';
import { QUERY_KEYS } from '@/api/invalidation';

export function useAnalyticsTotal(filters: FilterInput = {}) {
  const sanitized = sanitizeFilters(filters);
  return useQuery({
    queryKey: ['analytics', 'total', sanitized],
    queryFn: (): Promise<TotalOut> => totalAnalyticsTotalGet(sanitized),
  });
}

export function useAnalyticsSummary(
  groupBy: 'category' | 'owner' | 'month' | 'account' | 'merchant' | 'subcategory',
  filters: FilterInput = {},
  limit?: number
) {
  const sanitized = sanitizeFilters(filters);
  return useQuery({
    queryKey: ['analytics', 'summary', { groupBy, ...sanitized, limit }],
    queryFn: (): Promise<GroupSummaryPage> =>
      summaryAnalyticsSummaryGet({ group_by: groupBy, limit, ...sanitized }),
  });
}

export function useAnalyticsByCategory(filters: FilterInput = {}, limit?: number) {
  const sanitized = sanitizeFilters(filters);
  return useQuery({
    queryKey: ['analytics', 'by-category', { ...sanitized, limit }],
    queryFn: (): Promise<GroupSummaryPage> =>
      byCategoryAnalyticsByCategoryGet({ limit, ...sanitized }),
  });
}

export function useAnalyticsByMonth(filters: FilterInput = {}, limit?: number) {
  const sanitized = sanitizeFilters(filters);
  return useQuery({
    queryKey: ['analytics', 'by-month', { ...sanitized, limit }],
    queryFn: (): Promise<GroupSummaryPage> =>
      byMonthAnalyticsByMonthGet({ limit, ...sanitized }),
  });
}

export function useAnalyticsByOwner(filters: FilterInput = {}, limit?: number) {
  const sanitized = sanitizeFilters(filters);
  return useQuery({
    queryKey: ['analytics', 'by-owner', { ...sanitized, limit }],
    queryFn: (): Promise<GroupSummaryPage> =>
      byOwnerAnalyticsByOwnerGet({ limit, ...sanitized }),
  });
}

export function useAnalyticsByAccount(filters: FilterInput = {}, limit?: number) {
  return useAnalyticsSummary('account', filters, limit);
}

export function useAnalyticsByMerchant(filters: FilterInput = {}, limit?: number) {
  return useAnalyticsSummary('merchant', filters, limit);
}

export function useAnalyticsBySubcategory(filters: FilterInput = {}, limit?: number) {
  const sanitized = sanitizeFilters(filters);
  return useQuery({
    queryKey: ['analytics', 'by-subcategory', { ...sanitized, limit }],
    queryFn: (): Promise<GroupSummaryPage> =>
      bySubcategoryAnalyticsBySubcategoryGet({ limit, ...sanitized }),
  });
}

export function useAnalyticsTopMerchants(filters: FilterInput = {}, limit: number = 10) {
  const sanitized = sanitizeFilters(filters);
  return useQuery({
    queryKey: ['analytics', 'top-merchants', { ...sanitized, limit }],
    queryFn: (): Promise<MerchantSummary[]> =>
      topMerchantsAnalyticsTopMerchantsGet({ limit, ...sanitized }),
  });
}

export function useAnalyticsLargest(filters: FilterInput = {}, limit: number = 10) {
  const sanitized = sanitizeFilters(filters);
  return useQuery({
    queryKey: ['analytics', 'largest', { ...sanitized, limit }],
    queryFn: (): Promise<TransactionListOut> =>
      largestAnalyticsLargestGet({ limit, ...sanitized }),
  });
}

export function useAnalyticsSearch(query: string, filters: FilterInput = {}, limit: number = 50) {
  const sanitized = sanitizeFilters(filters);
  return useQuery({
    queryKey: ['analytics', 'search', { query, ...sanitized, limit }],
    queryFn: (): Promise<TransactionListOut> =>
      searchAnalyticsSearchGet({ query, limit, ...sanitized }),
    enabled: Boolean(query && query.trim().length > 0),
  });
}

export function useAnalyticsValues(
  dimension: 'category' | 'subcategory' | 'merchant',
  filters: FilterInput = {},
  query?: string,
  limit: number = 25
) {
  const sanitized = sanitizeFilters(filters);
  return useQuery({
    queryKey: ['analytics', 'values', { dimension, query, ...sanitized, limit }],
    queryFn: (): Promise<ValueListOut> =>
      valuesAnalyticsValuesGet({
        dimension,
        query,
        limit,
        ...sanitized,
      }),
  });
}

export function useAnalyticsCashFlow(filters: FilterInput = {}) {
  const sanitized = sanitizeFilters(filters);
  return useQuery({
    queryKey: ['analytics', 'cash-flow', sanitized],
    queryFn: (): Promise<CashFlowOut> => cashFlowAnalyticsCashFlowGet(sanitized),
  });
}

/**
 * Unmapped values summary.
 * Note: Labeled ledger-wide — ignores all filters and does not run through _apply_filters.
 */
export function useAnalyticsUnmapped() {
  return useQuery({
    queryKey: QUERY_KEYS.unmapped,
    queryFn: (): Promise<UnmappedValuesOut> => unmappedValuesAnalyticsUnmappedGet(),
  });
}
