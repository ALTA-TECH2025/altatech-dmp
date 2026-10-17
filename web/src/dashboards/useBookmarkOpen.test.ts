import { describe, expect, it } from 'vitest';
import { normalizeQuery } from './useBookmarkOpen';

describe('normalizeQuery（書籤 → 分析頁狀態）', () => {
  it('事件指標 event null → 空字串、缺的陣列補回', () => {
    const base = { metrics: [{ event: '', agg: 'total' }], filters: [], group_by: [], time: { from: 'a', to: 'b' } };
    const out = normalizeQuery(base, 'events', { metrics: [{ event: null, agg: 'users' }], time: { from: '2026-10-01', to: '2026-10-16' } });
    expect(out.metrics).toEqual([{ event: '', agg: 'users' }]); expect(out.filters).toEqual([]); expect(out.group_by).toEqual([]); expect(out.time.from).toBe('2026-10-01');
  });
  it('其他分析原樣覆蓋', () => {
    expect(normalizeQuery({ steps: [], window: { value: 7, unit: 'day' } }, 'funnel', { steps: [{ event: 'a' }] }).steps).toEqual([{ event: 'a' }]);
  });
});
