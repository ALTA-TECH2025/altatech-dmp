import { describe, expect, it } from 'vitest';
import { candidates, demoFetch } from './fetch';

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
