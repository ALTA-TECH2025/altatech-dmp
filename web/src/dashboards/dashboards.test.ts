import { describe, expect, it } from 'vitest';
import { defaultTitle, firstGroup, metricLabels, tableColumns, timeText } from './dashboards';

describe('dashboards helpers', () => {
  it('事件指標別名與標籤', () => {
    const { aliases, labels } = metricLabels({ metrics: [{ event: 'bond_deal', agg: 'sum', field: 'amount' }, { event: null, agg: 'users', alias: 'u' }] });
    expect(aliases).toEqual(['m1', 'u']); expect(labels.m1).toBe('bond_deal 數值求和(amount)'); expect(labels.u).toBe('任一事件 觸發用戶數');
  });
  it('預設標題', () => {
    expect(defaultTitle('events', { metrics: [{ event: 'bond_order', agg: 'total' }] })).toBe('bond_order 總次數');
    expect(defaultTitle('funnel', { steps: [{ event: 'a', alias: '看盤' }, { event: 'b' }] })).toBe('看盤 → b');
    expect(defaultTitle('retention', { initial: { event: 'login' }, unit: 'week' })).toBe('login 留存（週）');
    expect(defaultTitle('path', { anchor: { event: 'login' }, steps: 4 })).toBe('login 之後 4 步');
    expect(defaultTitle('attribution', { target: { event: 'deal' }, models: ['first', 'linear'] })).toBe('deal 歸因（first／linear）');
  });
  it('漏斗第一組與表格欄位', () => {
    const g = firstGroup([{ bucket: '2026-10-01', g1: 'iOS', step: 1 }, { bucket: '2026-10-01', g1: 'iOS', step: 2 }, { bucket: '2026-10-02', g1: 'iOS', step: 1 }]);
    expect(g.key).toBe('2026-10-01 iOS'); expect(g.rows.length).toBe(2); expect(g.groups).toBe(2);
    expect(tableColumns(['bucket', 'm1', '_k', 'suppressed'], [])).toEqual(['bucket', 'm1']); expect(tableColumns(undefined, [{ a: 1, _k: 2 }])).toEqual(['a']);
  });
  it('時間文字', () => {
    expect(timeText(undefined, '7d')).toBe('最近 7 天');
    expect(timeText({ from: '2026-10-01', to: '2026-10-16', pinned: true }, '30d')).toBe('2026-10-01 ～ 2026-10-16（釘住）');
    expect(timeText({ from: '2026-09-17', to: '2026-10-16' }, '30d')).toBe('最近 30 天：2026-09-17 ～ 2026-10-16');
  });
});
