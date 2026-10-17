import { describe, expect, it } from 'vitest';
import { askRules, parseTime } from './ask';

const cat = { events: [{ name: 'bond_deal', cn: '證券_成交' }, { name: 'bond_order', cn: '證券_委託' }, { name: 'app_login_success', cn: 'APP登入成功' }, { name: 'hq_stockdetail_view', cn: '行情_個股明細瀏覽' }, { name: 'kh_apply', cn: '開戶_完成開戶申請' }], dimensions: [{ name: 'platform', cn: '平台' }, { name: 'channel', cn: '渠道' }] };
const today = new Date('2026-10-17T00:00:00Z'); const dt = { from: '2026-09-17', to: '2026-10-16', grain: 'day' };

describe('示範模式問數（與 backend/insight_agent/configure.py 同構）', () => {
  it('上週各平台成交金額 → events sum amount, group_by platform, 上週釘住', () => {
    const r = askRules('上週各平台成交金額', cat, { today, defaultTime: dt });
    const c = r.cards[0]; expect(c.kind).toBe('events'); expect(c.query.group_by).toEqual(['platform']);
    expect((c.query.metrics as { agg: string; field?: string }[])[0]).toMatchObject({ event: 'bond_deal', agg: 'sum', field: 'amount' });
    expect(c.query.time).toMatchObject({ from: '2026-10-05', to: '2026-10-11', pinned: true });
  });
  it('多句 → 多卡；漏斗步驟依出現順序；留存週', () => {
    const r = askRules('上週各平台成交金額、看盤到委託到成交的漏斗、登入後 4 週留存', cat, { today, defaultTime: dt });
    expect(r.cards.map((c) => c.kind)).toEqual(['events', 'funnel', 'retention']);
    expect((r.cards[1].query.steps as { event: string }[]).map((s) => s.event)).toEqual(['hq_stockdetail_view', 'bond_order', 'bond_deal']);
    expect(r.cards[2].query).toMatchObject({ unit: 'week', periods: 4 });
  });
  it('對不上 → unmatched；路徑之前；時間解析', () => {
    expect(askRules('幫我看一下天氣', cat, { today, defaultTime: dt }).cards).toEqual([]);
    expect(askRules('成交之前做什麼', cat, { today, defaultTime: dt }).cards[0].query.direction).toBe('before');
    expect(parseTime('9 月成交', today, dt)[0]).toMatchObject({ from: '2026-09-01', to: '2026-09-30' });
    expect(parseTime('近 7 天', today, dt)[0]).toMatchObject({ from: '2026-10-11', to: '2026-10-17' });
    expect(parseTime('昨天', today, dt)[0]).toMatchObject({ from: '2026-10-16', to: '2026-10-16' });
  });
});
