import { describe, expect, it } from 'vitest';
import { fmtNum, niceScale, seriesColor, toSeries } from './series';
import type { EventsResult } from './types';

const res = (rows: EventsResult['rows'], columns: string[]): EventsResult => ({ rows, columns, chart_spec: {}, sql: '', lineage: [], cost: { ms: 1, rows: rows.length, layer: 'L0' }, warnings: [], masked: {} });

describe('事件分析序列', () => {
  it('長表 → 指標 × 分組序列，依 bucket 排序，抑制列標記', () => {
    const r = res([
      { bucket: '2026-10-02', g1: 'iOS', m1: 5, _k: 30, suppressed: false }, { bucket: '2026-10-01', g1: 'iOS', m1: 3, _k: 30, suppressed: false },
      { bucket: '2026-10-01', g1: '', m1: null, _k: 4, suppressed: true },
    ], ['bucket', 'g1', 'm1', '_k', 'suppressed']);
    const s = toSeries(r, ['m1'], { m1: '次數' });
    expect(s.map((x) => x.label)).toEqual(['次數 · iOS', '次數 · （未填）']);
    expect(s[0].points.map((p) => p.x)).toEqual(['2026-10-01', '2026-10-02']);
    expect(s[1].points[0]).toEqual({ x: '2026-10-01', y: null, suppressed: true });
  });
  it('total 粒度：一個指標一條序列，分組值在類別軸、照結果順序（Top N）', () => {
    const s = toSeries(res([{ g1: 'B', m1: 2 }, { g1: 'A', m1: 1 }], ['g1', 'm1']), ['m1', 'm2'], { m1: '次數' });
    expect(s.map((x) => x.label)).toEqual(['次數', 'm2']);
    expect(s[0].points.map((p) => [p.x, p.y])).toEqual([['B', 2], ['A', 1]]);
    expect(toSeries(res([{ m1: 7 }], ['m1']), ['m1'])[0].points[0].x).toBe('合計');
  });
  it('類別色固定順序且不循環；刻度從 0 起算；數字格式', () => {
    expect(seriesColor(0)).toBe('var(--s1)'); expect(seriesColor(9)).toBe('var(--s8)');
    expect(niceScale(87)).toEqual({ max: 100, ticks: [0, 50, 100] });
    expect(niceScale(0).max).toBe(1);
    expect(fmtNum(12345)).toBe('1.2 萬'); expect(fmtNum(2.5)).toBe('2.50'); expect(fmtNum(null)).toBe('—');
  });
});
