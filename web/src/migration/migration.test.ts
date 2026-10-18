import { describe, expect, it } from 'vitest';
import { daysBetween, diffOk, diffText, timelineStatus } from './migration';

describe('migration helpers', () => {
  it('切換時序狀態', () => {
    expect(timelineStatus([{ date: '2026-11-20' }, { date: '2027-01-22' }, { date: '2027-09-30' }], '2027-01-01')).toEqual(['done', 'now', 'todo']);
    expect(timelineStatus([{ date: '2026-11-20' }], '2026-10-17')).toEqual(['now']);
  });
  it('差異與天數', () => { expect(diffText(-0.046)).toBe('-4.6%'); expect(diffText(0.02)).toBe('+2.0%'); expect(diffText(null)).toBe('—'); expect(daysBetween('2026-10-01', '2026-10-03')).toBe(3); });
  it('分群人數差異門檻', () => { expect(diffOk(-0.03)).toBe(true); expect(diffOk(0.08)).toBe(false); expect(diffOk(null)).toBeNull(); });
});
