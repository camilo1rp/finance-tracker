'use client';

import { useSearchParams, useRouter, usePathname } from 'next/navigation';
import { useCallback, useMemo } from 'react';
import { FilterInput, sanitizeFilters, getTodayIsoString } from '@/api/filters';

export function useFilterParams() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const filters = useMemo(() => {
    const date_from = searchParams.get('date_from');
    const date_to = searchParams.get('date_to') || getTodayIsoString(); // ALWAYS explicit date_to!
    const account_id = searchParams.get('account_id') ? Number(searchParams.get('account_id')) : undefined;
    const owner_id = searchParams.get('owner_id') ? Number(searchParams.get('owner_id')) : undefined;
    const merchant = searchParams.get('merchant') || undefined;
    const category = searchParams.get('category') || undefined;
    const subcategory = searchParams.get('subcategory') || undefined;
    const transaction_type = searchParams.get('transaction_type') || undefined;

    return sanitizeFilters({
      date_from,
      date_to,
      account_id,
      owner_id,
      merchant,
      category,
      subcategory,
      transaction_type,
    });
  }, [searchParams]);

  const setFilters = useCallback(
    (newFilters: FilterInput, options: { replace?: boolean } = {}) => {
      const sanitized = sanitizeFilters(newFilters);
      const params = new URLSearchParams();

      if (sanitized.date_from) params.set('date_from', sanitized.date_from);
      if (sanitized.date_to) params.set('date_to', sanitized.date_to); // ALWAYS explicit date_to!
      if (sanitized.account_id != null) params.set('account_id', String(sanitized.account_id));
      if (sanitized.owner_id != null) params.set('owner_id', String(sanitized.owner_id));
      if (sanitized.merchant) params.set('merchant', sanitized.merchant);
      if (sanitized.category) params.set('category', sanitized.category);
      if (sanitized.subcategory) params.set('subcategory', sanitized.subcategory);
      if (sanitized.transaction_type) params.set('transaction_type', sanitized.transaction_type);

      const qs = params.toString();
      const target = qs ? `${pathname}?${qs}` : pathname;

      if (options.replace) {
        router.replace(target);
      } else {
        router.push(target);
      }
    },
    [pathname, router]
  );

  return { filters, setFilters };
}

/**
 * Helper to build a deep-link URL to /transactions carrying an explicit date_to.
 */
export function buildTransactionsDeepLink(filters: FilterInput): string {
  const sanitized = sanitizeFilters(filters);
  const params = new URLSearchParams();

  if (sanitized.date_from) params.set('date_from', sanitized.date_from);
  params.set('date_to', sanitized.date_to); // Non-negotiable: deep links to /transactions carry an explicit date_to
  if (sanitized.account_id != null) params.set('account_id', String(sanitized.account_id));
  if (sanitized.owner_id != null) params.set('owner_id', String(sanitized.owner_id));
  if (sanitized.merchant) params.set('merchant', sanitized.merchant);
  if (sanitized.category) params.set('category', sanitized.category);
  if (sanitized.subcategory) params.set('subcategory', sanitized.subcategory);
  if (sanitized.transaction_type) params.set('transaction_type', sanitized.transaction_type);

  return `/transactions?${params.toString()}`;
}
