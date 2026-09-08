/**
 * Cache Invalidation Matrix for TanStack Query.
 * 
 * Invalidation Matrix Rules:
 * | Trigger Event                                      | Invalidate Transactions | Invalidate Analytics | Invalidate Unmapped | Invalidate Mappings |
 * |----------------------------------------------------|-------------------------|----------------------|---------------------|---------------------|
 * | Mapping Plan Apply (POST /mappings/apply)          | YES                     | YES                  | YES                 | YES                 |
 * | Mapping Patch (PATCH /mappings/{id})               | YES                     | YES                  | YES                 | YES                 |
 * | Mapping Delete (DELETE /mappings/{id})             | YES                     | YES                  | YES                 | YES                 |
 * | CSV Import (POST /imports)                         | YES                     | YES                  | YES                 | YES                 |
 * | Reclassify (POST /transactions/reclassify)         | YES                     | YES                  | YES                 | YES                 |
 * | Steward Approval Apply (POST /agent/resume approve)| YES                     | YES                  | YES                 | YES                 |
 * | Transaction Patch (PATCH /transactions/{id})       | YES (Optimistic update) | YES                  | YES                 | NO                  |
 * | Plain Mapping Create (POST /mappings)              | NO (Does not reclassify)| NO (INV-07)          | NO                  | YES                 |
 * 
 * Note on POST /mappings: Creating a rule via POST /mappings does NOT reclassify transactions (INV-07).
 * It invalidates only mappings; transactions and analytics remain untouched unless reclassify is chained.
 */

import { QueryClient } from '@tanstack/react-query';

export const QUERY_KEYS = {
  transactions: ['transactions'] as const,
  transactionDetail: (id: number) => ['transactions', id] as const,
  analytics: ['analytics'] as const,
  unmapped: ['analytics', 'unmapped'] as const,
  mappings: ['mappings'] as const,
  accounts: ['accounts'] as const,
  owners: ['owners'] as const,
  artifacts: ['artifacts'] as const,
  artifactDetail: (id: number) => ['artifacts', id] as const,
  artifactRows: (id: number) => ['artifacts', id, 'rows'] as const,
  agentThread: (threadId: string) => ['agent', 'thread', threadId] as const,
};

/**
 * Invalidate all queries affected by a ledger reclassification or new rows.
 */
export async function invalidateLedgerData(queryClient: QueryClient): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: QUERY_KEYS.transactions }),
    queryClient.invalidateQueries({ queryKey: QUERY_KEYS.analytics }),
    queryClient.invalidateQueries({ queryKey: QUERY_KEYS.mappings }),
  ]);
}

/**
 * Invalidate when a mapping rule is created via plain POST /mappings (no reclassify).
 */
export async function invalidateMappingsOnly(queryClient: QueryClient): Promise<void> {
  await queryClient.invalidateQueries({ queryKey: QUERY_KEYS.mappings });
}
