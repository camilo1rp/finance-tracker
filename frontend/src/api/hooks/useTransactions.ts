import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listTransactionsTransactionsGet,
  getTransactionTransactionsTransactionIdGet,
  patchTransactionTransactionsTransactionIdPatch,
  reclassifyTransactionsReclassifyPost,
} from '@/api/generated/transactions/transactions';
import {
  TransactionPage,
  TransactionOut,
  TransactionPatch,
  ReclassifyResultOut,
} from '@/api/generated/model';
import { FilterInput, sanitizeFilters } from '@/api/filters';
import { QUERY_KEYS, invalidateLedgerData } from '@/api/invalidation';

export function useTransactionsList(filters: FilterInput = {}, limit: number = 25) {
  const sanitized = sanitizeFilters(filters);

  return useQuery({
    queryKey: ['transactions', 'list', { ...sanitized, limit }],
    queryFn: (): Promise<TransactionPage> => {
      return listTransactionsTransactionsGet({
        date_from: sanitized.date_from,
        date_to: sanitized.date_to, // ALWAYS sent explicitly!
        account_id: sanitized.account_id,
        owner_id: sanitized.owner_id,
        merchant: sanitized.merchant,
        category: sanitized.category,
        subcategory: sanitized.subcategory,
        limit,
      });
    },
  });
}

export function useTransaction(transactionId: number) {
  return useQuery({
    queryKey: QUERY_KEYS.transactionDetail(transactionId),
    queryFn: (): Promise<TransactionOut> => {
      return getTransactionTransactionsTransactionIdGet(transactionId);
    },
    enabled: transactionId != null && !isNaN(transactionId),
  });
}

/**
 * Optimistic update: ONLY allowed on PATCH /transactions/{id}.
 * Applies dirty changes immediately to cache; rolls back on error.
 */
export function usePatchTransaction() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      transactionId,
      data,
    }: {
      transactionId: number;
      data: TransactionPatch;
    }): Promise<TransactionOut> => {
      return patchTransactionTransactionsTransactionIdPatch(transactionId, data);
    },
    onMutate: async ({ transactionId, data }) => {
      // Cancel queries to avoid overwriting optimistic update
      await queryClient.cancelQueries({ queryKey: QUERY_KEYS.transactionDetail(transactionId) });

      const previousDetail = queryClient.getQueryData<TransactionOut>(
        QUERY_KEYS.transactionDetail(transactionId)
      );

      if (previousDetail) {
        const optimistic: TransactionOut = {
          ...previousDetail,
          ...(data.category_override !== undefined && {
            category_override: data.category_override,
            effective_category: data.category_override ?? previousDetail.category_normalized ?? previousDetail.category_raw,
          }),
          ...(data.subcategory !== undefined && { subcategory: data.subcategory }),
          ...(data.merchant_override !== undefined && {
            merchant_override: data.merchant_override,
            effective_merchant: data.merchant_override ?? previousDetail.merchant_normalized ?? previousDetail.merchant_raw,
          }),
          ...(data.owner_id !== undefined && { owner_id: data.owner_id }),
          ...(data.type_override !== undefined && {
            type_override: data.type_override,
            effective_type: data.type_override ?? previousDetail.transaction_type,
            is_spend: (data.type_override ?? previousDetail.transaction_type) === 'SPEND',
          }),
        };
        queryClient.setQueryData(QUERY_KEYS.transactionDetail(transactionId), optimistic);
      }

      return { previousDetail };
    },
    onError: (_err, { transactionId }, context) => {
      if (context?.previousDetail) {
        queryClient.setQueryData(
          QUERY_KEYS.transactionDetail(transactionId),
          context.previousDetail
        );
      }
    },
    onSettled: async (_data, _error, { transactionId }) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: QUERY_KEYS.transactionDetail(transactionId) }),
        queryClient.invalidateQueries({ queryKey: QUERY_KEYS.transactions }),
        queryClient.invalidateQueries({ queryKey: QUERY_KEYS.analytics }),
      ]);
    },
  });
}

export function useReclassify() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (accountId?: number): Promise<ReclassifyResultOut> => {
      return reclassifyTransactionsReclassifyPost(
        accountId != null ? { account_id: accountId } : undefined
      );
    },
    onSuccess: async () => {
      await invalidateLedgerData(queryClient);
    },
  });
}
