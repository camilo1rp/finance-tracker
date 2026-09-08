/**
 * Centralized Money type and formatting utilities.
 * 
 * Rules:
 * - String-backed decimal Numeric(12,2).
 * - No code path parses with parseFloat or Number().
 * - All arithmetic uses Decimal from decimal.js.
 * - Centralized locale string formatting.
 */
import Decimal from 'decimal.js';

export type Money = string;

/**
 * Parse an incoming amount to a Decimal without float conversion.
 */
export function toDecimal(val: Money | string | null | undefined): Decimal {
  if (val === null || val === undefined || val === '') {
    return new Decimal('0.00');
  }
  return new Decimal(String(val).trim());
}

/**
 * Format a Money string to a display currency string (e.g. "$1,234.56" or "-$45.00").
 */
export function formatMoney(
  val: Money | string | null | undefined,
  options: {
    showSign?: boolean;
    currencySymbol?: string;
  } = {}
): string {
  const { showSign = false, currencySymbol = '$' } = options;
  if (val === null || val === undefined || val === '') {
    return `${currencySymbol}0.00`;
  }

  const d = toDecimal(val);
  const isNegative = d.isNegative();
  const isPositive = d.isPositive() && !d.isZero();
  const absFixed = d.abs().toFixed(2);

  const [intPart, decPart] = absFixed.split('.');
  const intFormatted = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  const formatted = `${intFormatted}.${decPart}`;

  if (isNegative) {
    return `-${currencySymbol}${formatted}`;
  }
  if (showSign && isPositive) {
    return `+${currencySymbol}${formatted}`;
  }
  return `${currencySymbol}${formatted}`;
}

/**
 * Format spend amount (positive spend displayed as $X.XX).
 */
export function formatSpend(val: Money | string | null | undefined): string {
  return formatMoney(val);
}

/**
 * Format net cash flow (+/- sign visible).
 */
export function formatCashFlow(val: Money | string | null | undefined): string {
  return formatMoney(val, { showSign: true });
}

/**
 * Add two money values without float conversion.
 */
export function addMoney(a: Money | string, b: Money | string): Money {
  return toDecimal(a).plus(toDecimal(b)).toFixed(2);
}

/**
 * Subtract two money values without float conversion.
 */
export function subtractMoney(a: Money | string, b: Money | string): Money {
  return toDecimal(a).minus(toDecimal(b)).toFixed(2);
}

/**
 * Check if a money value is zero.
 */
export function isZeroMoney(val: Money | string | null | undefined): boolean {
  return toDecimal(val).isZero();
}

/**
 * Check if a money value is negative.
 */
export function isNegativeMoney(val: Money | string | null | undefined): boolean {
  return toDecimal(val).isNegative();
}
