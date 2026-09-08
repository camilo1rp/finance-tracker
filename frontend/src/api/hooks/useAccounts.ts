import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { listAccountsAccountsGet, createAccountAccountsPost } from '@/api/generated/accounts/accounts';
import { AccountCreate, AccountOut } from '@/api/generated/model';
import { QUERY_KEYS } from '@/api/invalidation';

export function useAccountsList() {
  return useQuery({
    queryKey: QUERY_KEYS.accounts,
    queryFn: () => listAccountsAccountsGet(),
  });
}

export function useCreateAccount() {
  const queryClient = useQueryClient();

  return useMutation({
    mutationFn: (data: AccountCreate): Promise<AccountOut> => {
      return createAccountAccountsPost(data);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.accounts });
    },
  });
}
