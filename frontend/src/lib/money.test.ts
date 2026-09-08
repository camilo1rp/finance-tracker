import { describe, expect, it } from 'vitest';
import {
  addMoney,
  formatCashFlow,
  formatMoney,
  formatSpend,
  isNegativeMoney,
  isZeroMoney,
  subtractMoney,
  toDecimal,
} from './money';

describe('Money utility and formatting', () => {
  it('formats basic amounts to 2 decimal places with currency symbol', () => {
    expect(formatMoney('1234.56')).toBe('$1,234.56');
    expect(formatMoney('0')).toBe('$0.00');
    expect(formatMoney(null)).toBe('$0.00');
    expect(formatMoney(undefined)).toBe('$0.00');
    expect(formatMoney('0.00')).toBe('$0.00');
  });

  it('formats negative amounts with preceding minus before currency symbol', () => {
    expect(formatMoney('-45.50')).toBe('-$45.50');
    expect(formatMoney('-1234567.89')).toBe('-$1,234,567.89');
  });

  it('formats positive cash flows with plus sign when requested', () => {
    expect(formatCashFlow('100.50')).toBe('+$100.50');
    expect(formatCashFlow('-100.50')).toBe('-$100.50');
    expect(formatCashFlow('0.00')).toBe('$0.00');
  });

  it('avoids IEEE 754 floating point arithmetic inaccuracies (e.g. 0.1 + 0.2 = 0.30)', () => {
    const result = addMoney('0.10', '0.20');
    expect(result).toBe('0.30');

    const subResult = subtractMoney('1.00', '0.90');
    expect(subResult).toBe('0.10');
  });

  it('checks zero and negative accurately', () => {
    expect(isZeroMoney('0')).toBe(true);
    expect(isZeroMoney('0.00')).toBe(true);
    expect(isZeroMoney('-0.00')).toBe(true);
    expect(isZeroMoney('1.00')).toBe(false);

    expect(isNegativeMoney('-0.01')).toBe(true);
    expect(isNegativeMoney('0.00')).toBe(false);
    expect(isNegativeMoney('10.00')).toBe(false);
  });
});
