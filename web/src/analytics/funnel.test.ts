import { describe, expect, it } from 'vitest';
import { dedupe } from './FunnelPage';

describe('漏斗頁目錄合併', () => {
  it('事件欄位與維度同名時只留第一個', () => {
    expect(dedupe([{ v: 'platform', cn: '平台' }, { v: 'symbol', cn: '商品' }, { v: 'platform', cn: '平台（維度）' }]).map((x) => x.cn)).toEqual(['平台', '商品']);
  });
});

import { funnelCsv, pct, summarize, trend } from './funnel';
import type { FunnelRow } from './types';
const row = (p: Partial<FunnelRow> & { step: number; users: number | null }): FunnelRow => ({ alias: `s${p.step}`, event: `e${p.step}`, _k: 100, rate_prev: null, rate_first: null, ...p });
describe('漏斗彙總', () => {
  it('趨勢桶相加、總轉化率、最大流失步、流失數', () => {
    const rows = [row({ bucket: 'd1', step: 1, users: 100 }), row({ bucket: 'd1', step: 2, users: 40 }), row({ bucket: 'd1', step: 3, users: 30 }), row({ bucket: 'd2', step: 1, users: 100 }), row({ bucket: 'd2', step: 2, users: 60 }), row({ bucket: 'd2', step: 3, users: 10 })];
    const s = summarize(rows)[0];
    expect(s.key).toBe(''); expect(s.steps.map((x) => x.users)).toEqual([200, 100, 40]); expect(s.total_rate).toBe(0.2);
    expect(s.steps[1].lost).toBe(100); expect(s.steps[1].lost_rate).toBe(0.5); expect(s.steps[2].rate_prev).toBe(0.4); expect(s.max_drop_step).toBe(3); expect(s.max_drop_rate).toBeCloseTo(0.6);
    const t = trend(rows); expect(t.map((x) => [x.bucket, x.total_rate])).toEqual([['d1', 0.3], ['d2', 0.1]]);
  });
  it('抑制列整步抑制；對照組與分組各自一條', () => {
    const rows = [row({ cohort: 'A', step: 1, users: 50 }), row({ cohort: 'A', step: 2, users: null, suppressed: true }), row({ cohort: 'B', g1: 'iOS', step: 1, users: 10 }), row({ cohort: 'B', g1: 'iOS', step: 2, users: 5 })];
    const s = summarize(rows); expect(s.map((x) => x.key)).toEqual(['A', 'B · iOS']);
    expect(s[0].steps[1].suppressed).toBe(true); expect(s[0].total_rate).toBeNull(); expect(s[1].total_rate).toBe(0.5);
    expect(pct(0.1234)).toBe('12.3%'); expect(funnelCsv(['a'], [[1], ['x,y']]).split('\r\n')[2]).toBe('"x,y"');
  });
});
