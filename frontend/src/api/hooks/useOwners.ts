import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listOwnersOwnersGet, createOwnerOwnersPost } from '@/api/generated/owners/owners';
import { OwnerCreate, OwnerOut } from '@/api/generated/model';
import { QUERY_KEYS } from '@/api/invalidation';
import axios from 'axios';

export function useOwnersList() {
  return useQuery({
    queryKey: QUERY_KEYS.owners,
    queryFn: () => listOwnersOwnersGet(),
  });
}

export function useCreateOwner() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: async (data: OwnerCreate): Promise<OwnerOut> => {
      try {
        return await createOwnerOwnersPost(data);
      } catch (err: any) {
        if (axios.isAxiosError(err) && err.response?.status === 409) {
          const customErr = new Error(`An owner with name "${data.name}" already exists.`);
          (customErr as any).isDuplicate = true;
          (customErr as any).status = 409;
          throw customErr;
        }
        throw err;
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.owners });
    },
  });
}
