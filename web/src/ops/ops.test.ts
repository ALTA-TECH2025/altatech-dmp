import { describe, expect, it } from 'vitest';
import { alertText, countBySeverity, fmtBytes, fmtMs, fmtRows, lagText, overall, ruleThreshold, slaStatus } from './ops';
import type { Alert } from './types';

const a = (p: Partial<Alert>): Alert => ({ key: 'k', cn: '來源延遲', severity: 'high', threshold: 120, op: '>', unit: 'min', text: '', value: 240, subject: '交易委託', firing: true, ...p });

describe('ops helpers', () => {
  it('fmtMs / fmtBytes / fmtRows / lagText', () => {
    expect(fmtMs(null)).toBe('—'); expect(fmtMs(812.5)).toBe('813 ms'); expect(fmtMs(1200)).toBe('1.2 s'); expect(fmtMs(12000)).toBe('12 s'); expect(fmtMs(90_000)).toBe('1.5 分');
    expect(fmtBytes(0)).toBe('0 B'); expect(fmtBytes(48_000_000)).toBe('48.0 MB'); expect(fmtBytes(7.2e12)).toBe('7.20 TB'); expect(fmtBytes(null)).toBe('—');
    expect(fmtRows(120)).toBe('120'); expect(fmtRows(120_000)).toBe('12.0 萬'); expect(fmtRows(7_000_000)).toBe('700 萬'); expect(fmtRows(7e9)).toBe('70.0 億');
    expect(lagText(12)).toBe('12 分鐘'); expect(lagText(240)).toBe('4.0 小時'); expect(lagText(3000)).toBe('2.1 天'); expect(lagText(null)).toBe('—');
  });
  it('slaStatus', () => {
    expect(slaStatus({ p95_ms: 900, sla_ms: 3000 })).toBe('ok'); expect(slaStatus({ p95_ms: 4000, sla_ms: 3000 })).toBe('warn'); expect(slaStatus({ p95_ms: 12000, sla_ms: 3000 })).toBe('error'); expect(slaStatus({ p95_ms: null, sla_ms: 3000 })).toBe('unknown'); expect(slaStatus({ p95_ms: 10, sla_ms: null })).toBe('unknown');
  });
  it('alertText / ruleThreshold', () => {
    expect(alertText(a({}))).toBe('來源延遲 · 交易委託：240 min（門檻 > 120 min）');
    expect(alertText(a({ cn: '隔離率', unit: '比例', value: 0.1, threshold: 0.05, subject: '交易委託' }))).toBe('隔離率 · 交易委託：10.0%（門檻 > 5.0%）');
    expect(alertText(a({ cn: '查詢 P95 超標', unit: '倍 SLA', value: 1.2, threshold: 1, subject: '漏斗分析' }))).toBe('查詢 P95 超標 · 漏斗分析：1.20 倍（門檻 > 1 倍）');
    expect(ruleThreshold({ key: 'x', cn: 'x', severity: 'low', threshold: 0.02, op: '>', unit: '比例', text: '' })).toBe('> 2%');
    expect(ruleThreshold({ key: 'x', cn: 'x', severity: 'low', threshold: 1, op: '>=', unit: '筆', text: '' })).toBe('>= 1 筆');
  });
  it('overall / countBySeverity', () => {
    expect(overall(['ok', 'warn'])).toBe('warn'); expect(overall(['ok', 'error', 'warn'])).toBe('error'); expect(overall(['unknown', 'unknown'])).toBe('unknown'); expect(overall(['ok', 'unknown'])).toBe('ok'); expect(overall([])).toBe('ok');
    expect(countBySeverity([a({}), a({ severity: 'critical' }), a({})])).toEqual({ high: 2, critical: 1 });
  });
});
