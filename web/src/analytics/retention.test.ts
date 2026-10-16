import { describe, expect, it } from 'vitest';
import { cellStyle, cellText } from './RetentionPage';
import type { RetentionRow } from './types';

const row = (o: Partial<RetentionRow>): RetentionRow => ({ cohort: '2026-10-01', period: 1, users: 100, retained: 40, rate: 0.4, complete: true, suppressed: false, ...o });

describe('留存矩陣格子', () => {
  it('比例／人數、未過期、抑制', () => {
    expect(cellText(row({}), 20, 'rate')).toBe('40.0%');
    expect(cellText(row({}), 20, 'users')).toBe('40');
    expect(cellText(row({ complete: false, rate: null, retained: null }), 20, 'rate')).toBe('—');
    expect(cellText(row({ suppressed: true, rate: null }), 20, 'rate')).toBe('< 20');
    expect(cellText(undefined, 20, 'rate')).toBe('');
  });
  it('順序色：比例越高越深，灰階給未過期與抑制', () => {
    expect(cellStyle(row({ rate: 0.9 })).color).toBe('#fff');
    expect(cellStyle(row({ rate: 0.1 })).color).toBe('var(--ink)');
    expect(cellStyle(row({ complete: false })).background).toBe('var(--soft)');
  });
});
