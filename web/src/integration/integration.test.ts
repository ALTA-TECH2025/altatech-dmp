import { describe, expect, it } from 'vitest';
import { isSecretRef, looksLikeSecret, parseList, queuedResult, rotationText, runText, RUN_STATUS_CN, scheduleBody } from './integration';
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
  it('立即執行排隊：找到該筆且結束才回結果（#87）', () => {
    const r = (id: number, status: string): SyncRun => ({ id, task_id: 't', scheduled_for: '2026-10-28T16:00:00+00:00', attempt: 1, status, trigger: 'manual', started_at: null, finished_at: null, rows_in: status === 'success' ? 300 : null, rows_ok: null, rows_quarantined: null, rows_rejected: null, rows_loaded: null, error: null, log: [], watermark_from: null, watermark_to: null });
    expect(queuedResult([], 7)).toBeNull();                                        // 還沒出現在紀錄裡
    for (const s of ['queued', 'running', 'retry_wait', 'interrupted']) expect(queuedResult([r(7, s)], 7)).toBeNull();
    expect(queuedResult([r(8, 'success'), r(7, 'queued')], 7)).toBeNull();        // 別筆結束不算
    expect(queuedResult([r(7, 'success')], 7)?.rows_in).toBe(300);
    expect(queuedResult([r(7, 'failed')], 7)?.status).toBe('failed');
    expect(RUN_STATUS_CN.queued).toBe('已排入佇列');
  });
});
