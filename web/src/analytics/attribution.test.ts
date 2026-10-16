import { describe, expect, it } from 'vitest';
import { shareSeries } from './AttributionPage';
import type { AttributionResult } from './types';

const res: AttributionResult = {
  rows: [
    { touch: '瀏覽', event: '$pageview', split: null, models: { first: { conversions: 60, value: 0, share: 0.6 }, linear: { conversions: 45.5, value: 0, share: 0.455 } }, users: 58, suppressed: false },
    { touch: '登入', event: 'app_login_success', split: null, models: { first: { conversions: null, value: null, share: null }, linear: { conversions: null, value: null, share: null } }, users: null, suppressed: true },
  ],
  summary: { conversions: 100, converters: 80, touched: 70, untouched: 30, value: 0, avg_touches: 1.8 }, models: ['first', 'linear'], sql: '', lineage: [], cost: { ms: 1, rows: 2, layer: 'L0' }, warnings: [], chart_spec: {},
};
describe('attribution share series', () => {
  it('one series per model, share in percent, suppressed rows null', () => {
    const s = shareSeries(res);
    expect(s.map((x) => x.label)).toEqual(['首次', '線性']);
    expect(s[0].points).toEqual([{ x: '瀏覽', y: 60, suppressed: false }, { x: '登入', y: null, suppressed: true }]);
    expect(s[1].points[0].y).toBe(45.5);
  });
});
