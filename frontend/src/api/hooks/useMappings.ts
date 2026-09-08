import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  listMappingsMappingsGet,
  createMappingMappingsPost,
  applyApprovedPlanMappingsApplyPost,
  previewMappingPlanMappingsPreviewPost,
  patchMappingMappingsMappingIdPatch,
  deleteMappingMappingsMappingIdDelete,
} from '@/api/generated/mappings/mappings';
import { reclassifyTransactionsReclassifyPost } from '@/api/generated/transactions/transactions';
import {
  NormalizationMappingCreate,
  NormalizationMappingOut,
  MappingPlanIn,
  MappingPreview,
  ApplyResult,
  MappingPatchIn,
  MappingPatchOut,
  MappingDeleteOut,
} from '@/api/generated/model';
import {
  QUERY_KEYS,
  invalidateLedgerData,
  invalidateMappingsOnly,
} from '@/api/invalidation';

/**
 * List mappings. If accountId is provided, executes TWO parallel calls:
 * 1. Account-scoped mappings (GET /mappings?account_id=...)
 * 2. Global mappings (GET /mappings)
 * and merges them, ensuring global rules (account_id IS NULL) are not excluded.
 */
export function useMappingsList(accountId?: number, kind?: string) {
  return useQuery({
    queryKey: ['mappings', { accountId, kind }],
    queryFn: async (): Promise<NormalizationMappingOut[]> => {
      if (accountId == null) {
        return await listMappingsMappingsGet({ kind: kind as any });
      }

      // Two calls merged
      const [accountScoped, globalScoped] = await Promise.all([
        listMappingsMappingsGet({ account_id: accountId, kind: kind as any }),
        listMappingsMappingsGet({ kind: kind as any }),
      ]);

      const seen = new Set<number>();
      const merged: NormalizationMappingOut[] = [];

      for (const m of [...accountScoped, ...globalScoped]) {
        if (!seen.has(m.id)) {
          seen.add(m.id);
          merged.push(m);
        }
      }

      return merged;
    },
  });
}

/**
 * Create mapping rule via POST /mappings.
 * Note: Does NOT reclassify by default (INV-07).
 * If chainReclassify=true, runs POST /transactions/reclassify and invalidates ledger data.
 */
export function useCreateMapping() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      data,
      chainReclassify = false,
    }: {
      data: NormalizationMappingCreate;
      chainReclassify?: boolean;
    }): Promise<{ mapping: NormalizationMappingOut; reclassified: boolean }> => {
      const mapping = await createMappingMappingsPost(data);
      if (chainReclassify) {
        await reclassifyTransactionsReclassifyPost();
        return { mapping, reclassified: true };
      }
      return { mapping, reclassified: false };
    },
    onSuccess: async (_, { chainReclassify }) => {
      if (chainReclassify) {
        await invalidateLedgerData(queryClient);
      } else {
        await invalidateMappingsOnly(queryClient);
      }
    },
  });
}

/**
 * Preview a mapping plan (pessimistic, pure read).
 */
export function usePreviewMappingPlan() {
  return useMutation({
    mutationFn: (plan: MappingPlanIn): Promise<MappingPreview> => {
      return previewMappingPlanMappingsPreviewPost(plan);
    },
  });
}

/**
 * Apply a mapping plan (pessimistic, reclassifies in service).
 */
export function useApplyMappingPlan() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (plan: MappingPlanIn): Promise<ApplyResult> => {
      return applyApprovedPlanMappingsApplyPost(plan);
    },
    onSuccess: async () => {
      await invalidateLedgerData(queryClient);
    },
  });
}

/**
 * Patch an existing mapping.
 */
export function usePatchMapping() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: ({
      mappingId,
      data,
    }: {
      mappingId: number;
      data: MappingPatchIn;
    }): Promise<MappingPatchOut> => {
      return patchMappingMappingsMappingIdPatch(mappingId, data);
    },
    onSuccess: async () => {
      await invalidateLedgerData(queryClient);
    },
  });
}

/**
 * Delete a mapping.
 */
export function useDeleteMapping() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (mappingId: number): Promise<MappingDeleteOut> => {
      return deleteMappingMappingsMappingIdDelete(mappingId);
    },
    onSuccess: async () => {
      await invalidateLedgerData(queryClient);
    },
  });
}
