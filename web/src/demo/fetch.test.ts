import { describe, expect, it } from 'vitest';
import { FIXED_NOTE_TEXT, candidates, demoFetch } from './fetch';

describe('demo fetch 候選鍵', () => {
  it('動態 id 換成 *，字面子資源保留', () => {
    expect(candidates('GET', '/v1/meta/events/bond_order', '')).toContain('GET /v1/meta/events/*');
    expect(candidates('GET', '/v1/segments/seg_1/versions/latest/members', '?page_size=50')).toContain('GET /v1/segments/*/versions/latest/members?page_size=50');
    expect(candidates('POST', '/v1/dashboards/d_1/cards/c_2/data', '')).toContain('POST /v1/dashboards/*/cards/*/data');
    expect(candidates('GET', '/v1/semantic/catalog', '')[0]).toBe('GET /v1/semantic/catalog');
  });
});

describe('示範：範本下載／上傳', () => {
  it('匯出目前內容有表頭與事件；上傳只預覽', async () => {
    const csv = await (await demoFetch('/v1/meta/templates/events?filled=true')).json() as string;
    expect(csv.startsWith('﻿name,cn,group')).toBe(true);
    expect(csv).toContain('bond_order');
    const r = await (await demoFetch('/v1/meta/templates/events/import?dry_run=true', { method: 'POST', body: JSON.stringify({ csv: 'name,cn\nbond_order,x\n#skip,y\nnew_evt,z\n' }) })).json() as { applied: boolean; rows: { key: string; action: string; line: number }[] };
    expect(r.applied).toBe(false);
    expect(r.rows).toEqual([{ line: 2, key: 'bond_order', action: 'unchanged', changes: [], errors: [] }, { line: 4, key: 'new_evt', action: 'create', changes: [], errors: [] }]);
  });
});

describe('示範：範例儀表板卡片用擷取的真實結果', () => {
  it('固定儀表板的卡片資料不改成瀏覽器合成值', async () => {
    const fx = (await import('./fixtures.json')).default as unknown as { routes: Record<string, unknown> };
    const k = Object.keys(fx.routes).find((x) => /^POST \/v1\/dashboards\/d_[^/]+\/cards\/[^/]+\/data$/.test(x) && (fx.routes[x] as { card?: { kind?: string } }).card?.kind === 'events');
    expect(k).toBeTruthy();
    const url = k!.split(' ')[1];
    const got = await (await demoFetch(url, { method: 'POST', body: '{}' })).json();
    expect(got).toEqual(fx.routes[k!]);
  });
});

describe('示範：同步任務立即執行只排隊', () => {
  it('回 202 queued、帶任務中文名、標示 demo', async () => {
    const res = await demoFetch('/v1/integration/sync-tasks/sync_orders/run', { method: 'POST' });
    const b = await res.json() as { status: string; run_id: number | null; demo: boolean; cn: string; task_id: string };
    expect(res.status).toBe(202);
    expect(b).toMatchObject({ task_id: 'sync_orders', status: 'queued', run_id: null, demo: true });
    expect(b.cn).not.toBe('');
  });
});

describe('示範：事件分析依粒度彙總', () => {
  type R = { rows: Record<string, unknown>[]; columns: string[]; chart_spec: { type: string } };
  const ask = async (grain: string, extra: Record<string, unknown> = {}) => (await (await demoFetch('/v1/analytics/events', { method: 'POST', body: JSON.stringify({ metrics: [{ event: 'deal', agg: 'total', alias: 'deals' }, { event: 'deal', agg: 'users', alias: 'deal_users' }], time: { from: '2026-01-01', to: '2026-10-30', grain }, ...extra }) })).json()) as R;
  it('按月：每月一列、bucket 是月初，不再回按天的固定資料', async () => {
    const r = await ask('month');
    expect(r.rows.map((x) => x.bucket)).toEqual(['2026-01-01', '2026-02-01', '2026-03-01', '2026-04-01', '2026-05-01', '2026-06-01', '2026-07-01', '2026-08-01', '2026-09-01', '2026-10-01']);
    expect(r.columns).not.toContain('g1');
  });
  it('按週：bucket 都是週一；按日：每天一列', async () => {
    const w = await ask('week');
    expect(w.rows.every((x) => new Date(`${String(x.bucket)}T00:00:00Z`).getUTCDay() === 1)).toBe(true);
    expect((await ask('day')).rows).toHaveLength(303);
  });
  it('合計：一列、沒有 bucket、數字卡（同後端）；人數類不超過次數加總', async () => {
    const t = await ask('total');
    expect(t.rows).toHaveLength(1);
    expect(t.rows[0]).not.toHaveProperty('bucket');
    expect(t.chart_spec.type).toBe('stat');
    expect(Number(t.rows[0].deal_users)).toBeLessThan(Number(t.rows[0].deals));
  });
  it('有分組才有 g1，每個桶三組', async () => {
    const g = await ask('month', { group_by: [{ field: '$platform' }] });
    expect(g.columns).toContain('g1');
    expect(g.rows).toHaveLength(30);
  });
});

describe('示範：看板卡片臨時改粒度', () => {
  it('事件卡改成按月 → 依月彙總，不沿用擷取的按天結果', async () => {
    const dash = await (await demoFetch('/v1/dashboards/d_seed_bond')).json() as { cards: { id: string; kind: string; query: { time: { from: string; to: string } } }[] };
    const card = dash.cards.find((c) => c.kind === 'events');
    expect(card).toBeTruthy();
    const d = await (await demoFetch(`/v1/dashboards/d_seed_bond/cards/${card!.id}/data`, { method: 'POST', body: JSON.stringify({ time: { from: '2026-08-01', to: '2026-10-15', grain: 'month' } }) })).json() as { rows: { bucket?: string }[] };
    expect([...new Set(d.rows.map((r) => r.bucket))]).toEqual(['2026-08-01', '2026-09-01', '2026-10-01']);
  });
});

describe('示範：還沒依條件合成的查詢要標示固定資料', () => {
  it('漏斗回應帶「固定範例資料」提示；事件分析不帶', async () => {
    const f = await (await demoFetch('/v1/analytics/funnel', { method: 'POST', body: JSON.stringify({ steps: [] }) })).json() as { warnings?: string[] };
    expect(f.warnings?.[0]).toBe(FIXED_NOTE_TEXT);
    const e = await (await demoFetch('/v1/analytics/events', { method: 'POST', body: JSON.stringify({ metrics: [{ event: 'deal', agg: 'total' }], time: { from: '2026-10-01', to: '2026-10-02', grain: 'day' } }) })).json() as { warnings: string[] };
    expect(e.warnings).not.toContain(FIXED_NOTE_TEXT);
  });
});

describe('示範：事件分析對比', () => {
  it('上一期與去年同期都帶 _prev 欄；不對比就沒有', async () => {
    const q = (compare?: string) => demoFetch('/v1/analytics/events', { method: 'POST', body: JSON.stringify({ metrics: [{ event: 'deal', agg: 'total', alias: 'deals' }], time: { from: '2026-10-01', to: '2026-10-03', grain: 'day' }, ...(compare ? { compare } : {}) }) }).then((r) => r.json() as Promise<{ columns: string[]; rows: Record<string, number>[] }>);
    for (const c of ['previous_period', 'last_year']) {
      const r = await q(c);
      expect(r.columns).toContain('deals_prev');
      expect(r.rows.every((x) => typeof x.deals_prev === 'number')).toBe(true);
    }
    expect((await q()).columns).not.toContain('deals_prev');
  });
});
