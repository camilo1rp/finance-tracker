/**
 * Shared ledger filter types and sanitization helpers.
 * 
 * Rules:
 * - date_to is ALWAYS sent explicitly (INV-26 trap: analytics defaults to today, but transactions does not).
 * - (unassigned) sentinel is translated to omit the category filter so the backend does not 422 with UnknownLabelFilterError.
 */

export interface SharedFilters {
  date_from?: string;
  date_to: string; // Non-optional! Explicit date_to required on all drill-downs & queries.
  account_id?: number;
  owner_id?: number;
  merchant?: string;
  transaction_type?: string;
  category?: string;
  subcategory?: string;
}

export interface FilterInput {
  date_from?: string | null;
  date_to?: string | null;
  account_id?: number | null;
  owner_id?: number | null;
  merchant?: string | null;
  transaction_type?: string | null;
  category?: string | null;
  subcategory?: string | null;
}

export const UNASSIGNED_SENTINEL = '(unassigned)';

/**
 * Return today's date formatted as YYYY-MM-DD in local time.
 */
export function getTodayIsoString(): string {
  const now = new Date();
  const year = now.getFullYear();
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

/**
 * Helper translating '(unassigned)' group_value sentinel into undefined
 * instead of passing it as a category filter — unknown labels raise 422 via UnknownLabelFilterError.
 */
export function sanitizeCategoryFilter(category?: string | null): string | undefined {
  if (!category) return undefined;
  const trimmed = category.trim();
  if (trimmed.toLowerCase() === UNASSIGNED_SENTINEL.toLowerCase()) {
    return undefined;
  }
  return trimmed;
}

/**
 * Ensure explicit date_to on any filter object.
 * If date_to is omitted, defaults explicitly to today's date to prevent implicit window widening.
 */
export function sanitizeFilters(input: FilterInput = {}): SharedFilters {
  const today = getTodayIsoString();

  return {
    date_from: input.date_from ? input.date_from.trim() : undefined,
    date_to: input.date_to ? input.date_to.trim() : today,
    account_id: input.account_id ?? undefined,
    owner_id: input.owner_id ?? undefined,
    merchant: input.merchant ? input.merchant.trim() : undefined,
    transaction_type: input.transaction_type ? input.transaction_type.trim() : undefined,
    category: sanitizeCategoryFilter(input.category),
    subcategory: sanitizeCategoryFilter(input.subcategory),
  };
}
