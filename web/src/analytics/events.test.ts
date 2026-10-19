import { describe, expect, it } from 'vitest';
import { chartAvailable, fmtValue, formulaError, growthOf, pieSlices, shareOf, transpose } from './events';
import type { Series } from './series';

describe('事件分析純函式', () => {
  it('數值格式', () => {
    expect(fmtValue(12345.678, 'int')).toBe('12,346'); expect(fmtValue(12345.678, 'dec2')).toBe('12,345.68');
    expect(fmtValue(45.678, 'pct0')).toBe('46%'); expect(fmtValue(45.678, 'pct2')).toBe('45.68%');
    expect(fmtValue(1234, 'twd0')).toBe('NT$ 1,234'); expect(fmtValue(12345, 'auto')).toBe('1.2 萬');
    expect(fmtValue('≥5000000', 'int')).toBe('≥5000000'); expect(fmtValue(null)).toBe('—');
  });
  it('公式檢查與後端同規則', () => {
    expect(formulaError('m1 / m2 * 100', ['m1', 'm2'])).toBeNull();
    expect(formulaError('m1 ** 2', ['m1'])).toBe('公式寫法有誤：* 前面少了數值或指標'); expect(formulaError('m1 ^ 2', ['m1'])).toBe('只能含指標別名、數字與 + - * / ( )'); expect(formulaError('(m1 + 1', ['m1'])).toBe('公式沒有寫完或括號不成對');
    expect(formulaError('m1 / m3', ['m1', 'm2'])).toBe('沒有這個指標別名：m3');
    expect(formulaError('  ', ['m1'])).toBe('公式不能是空的');
  });
  it('增長％：上期優先，否則同序列上一桶；佔比％：分組佔同桶、不分組佔整段', () => {
    const rows = [{ bucket: 'd1', g1: 'A', m1: 10 }, { bucket: 'd1', g1: 'B', m1: 30 }, { bucket: 'd2', g1: 'A', m1: 15 }, { bucket: 'd2', g1: 'B', m1: 15 }];
    expect(growthOf(rows, ['m1'], true).map((x) => x[0])).toEqual([null, null, 50, -50]);
    expect(growthOf([{ m1: 120, m1_prev: 100 }], ['m1'], false)[0][0]).toBe(20);
    expect(shareOf(rows, ['m1'], true).map((x) => x[0])).toEqual([25, 75, 50, 50]);
    expect(shareOf([{ bucket: 'd1', m1: 1 }, { bucket: 'd2', m1: 3 }], ['m1'], false).map((x) => x[0])).toEqual([25, 75]);
  });
  it('轉置與圖型可用性、圓餅切片', () => {
    const t = transpose([{ bucket: 'd1', m1: 1, m2: 2 }, { bucket: 'd2', m1: 3, m2: 4, suppressed: true }], ['m1', 'm2'], { m1: '次數' }, (r) => String(r.bucket));
    expect(t.head).toEqual(['指標', 'd1', 'd2']); expect(t.body).toEqual([['次數', '1', 'K'], ['m2', '2', 'K']]); expect(t.cut).toBe(0);
    const s: Series[] = [{ key: 'a', label: 'A', metric: 'm1', group: 'A', points: [{ x: 'd1', y: 1, suppressed: false }, { x: 'd2', y: 2, suppressed: false }] }, { key: 'b', label: 'B', metric: 'm1', group: 'B', points: [{ x: 'd1', y: 3, suppressed: false }] }];
    expect(chartAvailable('line', false, s)).toBe(false); expect(chartAvailable('stacked', true, s)).toBe(true); expect(chartAvailable('pie', true, [s[0]])).toBe(false);
    expect(pieSlices(s)).toEqual([{ name: 'A', value: 3 }, { name: 'B', value: 3 }]);
    expect(pieSlices([s[0]])).toEqual([{ name: 'd1', value: 1 }, { name: 'd2', value: 2 }]);
  });
});
