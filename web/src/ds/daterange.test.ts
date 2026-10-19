import { describe, expect, it } from 'vitest';
import { detectPreset, fmtRange, presetRange, previousPeriod, rangeDays } from './daterange';

const T = new Date(2026, 9, 19);   // 2026-10-19 週一

describe('快捷日期區間', () => {
  it('過去 N 天到昨天為止；今天／昨天', () => {
    expect(presetRange('7d', T)).toEqual({ from: '2026-10-12', to: '2026-10-18' });
    expect(presetRange('180d', T)).toEqual({ from: '2026-04-22', to: '2026-10-18' });
    expect(presetRange('today', T)).toEqual({ from: '2026-10-19', to: '2026-10-19' });
    expect(presetRange('yesterday', T)).toEqual({ from: '2026-10-18', to: '2026-10-18' });
  });
  it('週一起算的本週／上週；本月／上月；今年／去年', () => {
    expect(presetRange('this_week', T)).toEqual({ from: '2026-10-19', to: '2026-10-19' });
    expect(presetRange('last_week', T)).toEqual({ from: '2026-10-12', to: '2026-10-18' });
    expect(presetRange('this_week', new Date(2026, 9, 18))).toEqual({ from: '2026-10-12', to: '2026-10-18' });   // 週日
    expect(presetRange('this_month', T)).toEqual({ from: '2026-10-01', to: '2026-10-19' });
    expect(presetRange('last_month', T)).toEqual({ from: '2026-09-01', to: '2026-09-30' });
    expect(presetRange('last_month', new Date(2026, 0, 5))).toEqual({ from: '2025-12-01', to: '2025-12-31' });
    expect(presetRange('this_year', T).from).toBe('2026-01-01'); expect(presetRange('last_year', T)).toEqual({ from: '2025-01-01', to: '2025-12-31' });
  });
  it('反推快捷鍵、格式、天數、上一期', () => {
    expect(detectPreset({ from: '2026-10-12', to: '2026-10-18' }, T)).toBe('last_week');   // 同區間時取第一個（週）
    expect(detectPreset({ from: '2026-04-22', to: '2026-10-18' }, T)).toBe('180d');
    expect(detectPreset({ from: '2026-10-01', to: '2026-10-03' }, T)).toBeNull();
    expect(fmtRange({ from: '2026-10-12', to: '2026-10-18' })).toBe('2026.10.12 - 2026.10.18');
    expect(rangeDays({ from: '2026-10-12', to: '2026-10-18' })).toBe(7);
    expect(previousPeriod({ from: '2026-10-12', to: '2026-10-18' })).toEqual({ from: '2026-10-05', to: '2026-10-11' });
  });
});
