import { describe, expect, it } from 'vitest';
import { isSecretRef, looksLikeSecret, parseList, rotationText, runText, scheduleBody } from './integration';

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
