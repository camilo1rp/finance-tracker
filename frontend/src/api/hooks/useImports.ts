import { useMutation, useQueryClient } from '@tanstack/react-query';
import { ImportResult } from '@/api/generated/model';
import { invalidateLedgerData } from '@/api/invalidation';
import { AXIOS_INSTANCE } from '@/api/custom-instance';

export interface ImportCsvParams {
  accountId: number;
  file: File;
  allowDuplicates?: boolean;
}

export function useImportCsv() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async ({
      accountId,
      file,
      allowDuplicates = false,
    }: ImportCsvParams): Promise<ImportResult> => {
      const formData = new FormData();
      formData.append('file', file);

      const resp = await AXIOS_INSTANCE.post<ImportResult>('/imports', formData, {
        params: {
          account_id: accountId,
          allow_duplicates: allowDuplicates,
        },
        headers: {
          'Content-Type': 'multipart/form-data',
        },
      });

      return resp.data;
    },
    onSuccess: async () => {
      await invalidateLedgerData(queryClient);
    },
  });
}
