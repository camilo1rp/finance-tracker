import { describe, expect, it } from 'vitest';
import {
  sanitizeFilters,
  sanitizeCategoryFilter,
  UNASSIGNED_SENTINEL,
  getTodayIsoString,
} from './filters';

describe('API Filters & Sentinel Sanitization', () => {
  it('translates (unassigned) sentinel into undefined', () => {
    expect(sanitizeCategoryFilter('(unassigned)')).toBeUndefined();
    expect(sanitizeCategoryFilter('(UNASSIGNED)')).toBeUndefined();
    expect(sanitizeCategoryFilter('  (unassigned)  ')).toBeUndefined();
  });

  it('preserves valid category strings', () => {
    expect(sanitizeCategoryFilter('Dining')).toBe('Dining');
    expect(sanitizeCategoryFilter('  Groceries  ')).toBe('Groceries');
    expect(sanitizeCategoryFilter('')).toBeUndefined();
    expect(sanitizeCategoryFilter(null)).toBeUndefined();
  });

  it('ALWAYS provides an explicit date_to to prevent implicit window widening', () => {
    const today = getTodayIsoString();

    const empty = sanitizeFilters({});
    expect(empty.date_to).toBe(today);

    const withFromOnly = sanitizeFilters({ date_from: '2024-01-01' });
    expect(withFromOnly.date_from).toBe('2024-01-01');
    expect(withFromOnly.date_to).toBe(today);

    const withExplicitTo = sanitizeFilters({ date_from: '2024-01-01', date_to: '2024-01-31' });
    expect(withExplicitTo.date_to).toBe('2024-01-31');
  });

  it('translates (unassigned) in full filter objects', () => {
    const filters = sanitizeFilters({
      category: UNASSIGNED_SENTINEL,
      subcategory: UNASSIGNED_SENTINEL,
      account_id: 1,
    });
    expect(filters.category).toBeUndefined();
    expect(filters.subcategory).toBeUndefined();
    expect(filters.account_id).toBe(1);
    expect(filters.date_to).toBeDefined();
  });
});
