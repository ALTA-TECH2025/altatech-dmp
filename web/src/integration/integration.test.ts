import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { isSecretRef, looksLikeSecret, parseList, chainState, QUEUE_POLL_MS, QUEUE_SLOW_POLL_MS, rotationText, runText, RUN_STATUS_CN, runChain, scheduleBody, watchQueuedRun } from './integration';
import type { SyncRun } from './types';

describe('integration helpers', () => {
  it('secret ref 格式', () => {
    expect(isSecretRef('projects/p/secrets/crm-pg/versions/3')).toBe(true); expect(isSecretRef('kv/data/dmp/prod/dwh_trade#password')).toBe(true);
    expect(isSecretRef('secret://dmp/crm/password')).toBe(true); expect(isSecretRef('P@ssw0rd')).toBe(false); expect(isSecretRef('kv/data/dmp/prod/x#pass_2')).toBe(false);
  });
  it('疑似明文', () => {
    expect(looksLikeSecret('password', 'x')).toBe(true); expect(looksLikeSecret('host', 'sql.internal')).toBe(false);
    expect(looksLikeSecret('ca', '-----BEGIN CERT')).toBe(true); expect(looksLikeSecret('note', 'a'.repeat(41))).toBe(true);
  });
  it('輪替文字', () => {
    expect(rotationText({ kind: 'short_lived' }).pill).toBe('gray');
    expect(rotationText({ kind: 'rotating', every_days: 90, due: '2027-01-14', days_left: 89, state: 'ok' }).text).toContain('2027-01-14');
    expect(rotationText({ kind: 'rotating', every_days: 90, due: '2026-10-20', days_left: 3, state: 'warn' }).pill).toBe('amber');
    expect(rotationText({ kind: 'rotating', every_days: 90, due: '2026-10-01', days_left: -16, state: 'expired' }).text).toBe('已過期 16 天');
  });
  it('執行摘要與排程', () => {
    expect(runText(null)).toBe('尚未執行');
    expect(runText({ scheduled_for: '2026-10-17T02:56:00+00:00', status: 'success', rows_in: 300, rows_ok: 290, rows_quarantined: 10, rows_rejected: 0, rows_loaded: 290, attempt: 1, error: null })).toBe('成功 · 2026-10-17 02:56 · 讀 300 載入 290 隔離 10');
    expect(runText({ scheduled_for: '2026-10-17T02:56:00+00:00', status: 'failed', rows_in: null, rows_ok: null, rows_quarantined: null, rows_rejected: null, rows_loaded: null, attempt: 3, error: 'boom' })).toContain('boom');
    expect(scheduleBody('interval', '15')).toEqual({ interval_min: 15 }); expect(scheduleBody('interval', '0')).toBeNull();
    expect(scheduleBody('cron', '*/15 * * * *')).toEqual({ cron: '*/15 * * * *' }); expect(scheduleBody('cron', 'daily')).toBeNull();
    expect(parseList('ORD_NO, STATUS、x')).toEqual(['ORD_NO', 'STATUS', 'x']);
  });
});

// #87 立即執行：追整條重試鏈（審查 #91：重試與主控中斷接手都會開新紀錄；5 分鐘後仍要繼續追）
const SLOT = '2026-10-28T16:00:00+00:00';
const run = (id: number, status: string, o: Partial<SyncRun> = {}): SyncRun => ({ id, task_id: 't', scheduled_for: SLOT, attempt: 1, status, trigger: 'manual', started_at: null, finished_at: null, rows_in: null, rows_ok: null, rows_quarantined: null, rows_rejected: null, rows_loaded: null, error: null, log: [], watermark_from: null, watermark_to: null, ...o });
const retry = (id: number, status: string, o: Partial<SyncRun> = {}) => run(id, status, { trigger: 'retry', attempt: 2, ...o });

describe('立即執行的重試鏈', () => {
  it('正常成功、重試成功、重試用完、中斷接手、中斷且重試用完', () => {
    expect(chainState([], 1)).toEqual({ latest: null, done: false });                                        // 還沒出現
    for (const s of ['queued', 'running']) expect(chainState([run(1, s)], 1).done).toBe(false);
    expect(chainState([run(1, 'success', { rows_loaded: 7 })], 1)).toMatchObject({ done: true, latest: { id: 1, rows_loaded: 7 } });
    expect(chainState([run(1, 'retry_wait')], 1).done).toBe(false);                                          // 暫時失敗、等重試
    expect(chainState([run(1, 'retried'), retry(2, 'running')], 1)).toMatchObject({ done: false, latest: { id: 2 } });
    expect(chainState([run(1, 'retried', { rows_loaded: 0 }), retry(2, 'success', { rows_loaded: 7 })], 1)).toMatchObject({ done: true, latest: { id: 2, rows_loaded: 7 } });
    expect(chainState([run(1, 'retried'), retry(2, 'failed', { error: 'boom' })], 1)).toMatchObject({ done: true, latest: { status: 'failed', error: 'boom' } });
    expect(chainState([run(1, 'interrupted'), retry(2, 'retry_wait')], 1).done).toBe(false);                 // 主控中斷：後端同交易補一筆重試
    expect(chainState([run(1, 'interrupted'), retry(2, 'retried'), retry(3, 'success')], 1)).toMatchObject({ done: true, latest: { id: 3 } });
    expect(chainState([run(1, 'interrupted')], 1)).toMatchObject({ done: true, latest: { status: 'interrupted' } });   // 沒有補重試 = 重試已用完
    expect([RUN_STATUS_CN.queued, RUN_STATUS_CN.retried]).toEqual(['已排入佇列', '已重試']);
  });
  it('不混進別的窗口或別次執行', () => {
    const rows = [run(1, 'retried'), run(4, 'success', { trigger: 'schedule' }), retry(5, 'success', { scheduled_for: '2026-10-28T16:15:00+00:00' }), retry(0, 'failed'), retry(6, 'running')];
    expect(runChain(rows, 1).map((x) => x.id)).toEqual([1, 6]);
    expect(chainState(rows, 1)).toMatchObject({ done: false, latest: { id: 6 } });
  });
});

describe('輪詢跨過 5 分鐘仍繼續（假時鐘）', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-28T16:00:00Z')); });
  afterEach(() => { vi.useRealTimers(); });
  const watch = (seq: (min: number) => SyncRun[]) => {
    const t0 = Date.now(); const ups: { stale: boolean; id: number | null }[] = []; const done: SyncRun[] = [];
    const stop = watchQueuedRun({ runId: 1, fetchRuns: async () => seq((Date.now() - t0) / 60_000), onUpdate: (s) => ups.push({ stale: s.stale, id: s.latest?.id ?? null }), onDone: (r) => done.push(r) });
    return { ups, done, stop };
  };
  it('排隊超過 5 分鐘：標 stale、放慢但不停；worker 起來後成功仍會更新', async () => {
    const w = watch((m) => (m < 7 ? [run(1, 'queued')] : [run(1, 'success', { rows_loaded: 9 })]));
    await vi.advanceTimersByTimeAsync(4 * 60_000); expect(w.ups.at(-1)?.stale).toBe(false);
    await vi.advanceTimersByTimeAsync(2 * 60_000); expect(w.ups.at(-1)?.stale).toBe(true); expect(w.done).toEqual([]);
    const n = w.ups.length; await vi.advanceTimersByTimeAsync(QUEUE_SLOW_POLL_MS); expect(w.ups.length).toBe(n + 1);   // 放慢到 15 秒
    await vi.advanceTimersByTimeAsync(2 * 60_000); expect(w.done.map((r) => r.rows_loaded)).toEqual([9]);
    const after = w.ups.length; await vi.advanceTimersByTimeAsync(10 * QUEUE_POLL_MS); expect(w.ups.length).toBe(after);   // 結束就停
  });
  it('執行中超過 5 分鐘不警告；中途重試、最後成功取鏈尾那筆', async () => {
    const w = watch((m) => (m < 6 ? [run(1, 'running')] : m < 8 ? [run(1, 'retried'), retry(2, 'running')] : [run(1, 'retried'), retry(2, 'success', { rows_loaded: 7 })]));
    await vi.advanceTimersByTimeAsync(7 * 60_000); expect(w.ups.some((u) => u.stale)).toBe(false); expect(w.ups.at(-1)?.id).toBe(2);
    await vi.advanceTimersByTimeAsync(2 * 60_000); expect(w.done.map((r) => [r.id, r.rows_loaded])).toEqual([[2, 7]]);
  });
  it('讀取失敗下一輪再試；關閉面板就停', async () => {
    let calls = 0; const done: SyncRun[] = [];
    const stop = watchQueuedRun({ runId: 1, fetchRuns: async () => { calls += 1; if (calls === 1) throw new Error('net'); return [run(1, 'running')]; }, onUpdate: () => undefined, onDone: (r) => done.push(r) });
    await vi.advanceTimersByTimeAsync(2 * QUEUE_POLL_MS); expect(calls).toBe(2);
    stop(); await vi.advanceTimersByTimeAsync(10 * QUEUE_POLL_MS); expect(calls).toBe(2);
  });
});
