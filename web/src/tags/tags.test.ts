import { describe, expect, it } from 'vitest';
import { deliveryText, pushSummary, suggestKey, topDistribution, updateText } from './tags';
import type { Delivery, Push } from './types';

const push: Push = { id: 'p1', segment_id: 's1', segment_name: '高價值', destination_ids: ['d1'], mode: 'delta', schedule: 'after_batch', cron: '', include_tags: [], status: 'active', approval_id: null, owner: 'u', created_at: '', updated_at: '', last_delivered_version: 2, last_run_at: null, last_result: { version: 2, added: 5, removed: 1, batches: 1, ok: 1, failed: 0 }, runs: 1 };

describe('tags helpers', () => {
  it('updateText', () => {
    expect(updateText({ mode: 'manual', freq: 'daily', earliest: '06:00', keep: 1 })).toBe('手動');
    expect(updateText({ mode: 'routine', freq: 'weekly', earliest: '06:00', keep: 7 })).toBe('每週一 06:00 起 · 留 7 版');
  });
  it('pushSummary', () => {
    expect(pushSummary(push)).toBe('最近 v2：+5 −1，1 批（全部成功）；分群有新版本即自動增量');
    expect(pushSummary({ ...push, status: 'pending_approval' })).toBe('等待數位經營核可');
    expect(pushSummary({ ...push, schedule: 'cron', cron: '03:30', last_result: { ...push.last_result!, failed: 1 } })).toContain('1 批失敗）；每日 03:30');
  });
  it('deliveryText', () => {
    const d: Delivery = { id: 'dlv', push_id: 'p1', segment_id: 's1', destination_id: 'd1', version: 2, mode: 'delta', event: 'audience.delta', batch: { index: 1, total: 2, size: 5 }, members: 5, status: 'retrying', attempts: 1, next_retry_at: '2026-10-17T03:05:00+08:00', response: { status: 503, ms: 12, body: 'busy' }, created_at: '', updated_at: '', replay_of: null };
    expect(deliveryText(d)).toBe('增量名單 1/2（5 筆）· 503 · 12 ms · 下次重試 2026-10-17 03:05');
    expect(deliveryText({ ...d, status: 'ok', response: { status: null, ms: 0, body: 'ConnectError: dns' } })).toContain('連線失敗 · ConnectError: dns');
  });
  it('topDistribution merges tail into 其他', () => {
    const r = topDistribution([{ value: 'a', users: 5 }, { value: 'b', users: 50 }, { value: 'c', users: 1 }], 2);
    expect(r.rows.map((x) => x.value)).toEqual(['b', 'a', '其他']); expect(r.max).toBe(50);
  });
  it('suggestKey', () => {
    expect(suggestKey('Tier Grade')).toBe('tier_grade'); expect(suggestKey('價值分級')).toBe(''); expect(suggestKey('1abc')).toBe('');
  });
});
