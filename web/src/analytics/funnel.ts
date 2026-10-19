// 漏斗結果的純函式（有單元測試）：依（對照組, 分組）彙總各趨勢桶、總轉化率、最大流失步、流失數／流失率、趨勢列、CSV。
import type { FunnelRow } from './types';

export interface StepSummary { step: number; alias: string; event: string; users: number | null; suppressed: boolean; rate_prev: number | null; rate_first: number | null; lost: number | null; lost_rate: number | null }
export interface FunnelSummary { key: string; steps: StepSummary[]; total_rate: number | null; max_drop_step: number | null; max_drop_rate: number | null }

export const seriesKey = (r: FunnelRow): string => [r.cohort, r.g1].filter((x) => x != null && x !== '').map(String).join(' · ');

/** 每個（對照組, 分組）一條漏斗：趨勢桶的人數相加（一個用戶只落在最早做第一步的那個桶，相加不重複）；任一桶被抑制則整步抑制。 */
export function summarize(rows: FunnelRow[]): FunnelSummary[] {
  const by = new Map<string, Map<number, StepSummary>>();
  for (const r of rows) {
    const key = seriesKey(r);
    if (!by.has(key)) by.set(key, new Map());
    const m = by.get(key)!; const st = Number(r.step);
    const cur = m.get(st) ?? { step: st, alias: r.alias, event: r.event, users: 0, suppressed: false, rate_prev: null, rate_first: null, lost: null, lost_rate: null };
    if (r.suppressed || r.users === null || r.users === undefined) cur.suppressed = true; else cur.users = (cur.users ?? 0) + r.users;
    m.set(st, cur);
  }
  const out: FunnelSummary[] = [];
  for (const [key, m] of by) {
    const steps = Array.from(m.values()).sort((a, b) => a.step - b.step);
    let prev: number | null = null; const first = steps[0]?.suppressed ? null : (steps[0]?.users ?? null);
    let maxStep: number | null = null; let maxRate = -1;
    for (const s of steps) {
      const u = s.suppressed ? null : s.users;
      s.rate_prev = u !== null && prev !== null && prev > 0 ? u / prev : (s.step === 1 && u !== null ? 1 : null);
      s.rate_first = u !== null && first !== null && first > 0 ? u / first : (s.step === 1 && u !== null ? 1 : null);
      s.lost = u !== null && prev !== null && s.step > 1 ? prev - u : null;
      s.lost_rate = s.rate_prev !== null && s.step > 1 ? 1 - s.rate_prev : null;
      if (s.lost_rate !== null && s.lost_rate > maxRate) { maxRate = s.lost_rate; maxStep = s.step; }
      prev = u;
    }
    const last = steps[steps.length - 1];
    out.push({ key, steps, total_rate: last && !last.suppressed && last.users !== null && first ? (last.users as number) / first : null, max_drop_step: maxStep, max_drop_rate: maxStep === null ? null : maxRate });
  }
  return out;
}

export interface TrendRow { key: string; bucket: string; total_rate: number | null; users: (number | null)[]; rate_prev: (number | null)[] }
/** 趨勢：每（對照組, 分組）× 桶一列：總轉化率、各步人數與相對上一步。 */
export function trend(rows: FunnelRow[]): TrendRow[] {
  const by = new Map<string, FunnelRow[]>();
  for (const r of rows) { const k = `${seriesKey(r)}|${r.bucket ?? ''}`; by.set(k, [...(by.get(k) ?? []), r]); }
  const out: TrendRow[] = [];
  for (const [k, rs] of by) {
    const s = summarize(rs)[0]; const [key, bucket] = [k.slice(0, k.lastIndexOf('|')), k.slice(k.lastIndexOf('|') + 1)];
    out.push({ key, bucket, total_rate: s.total_rate, users: s.steps.map((x) => (x.suppressed ? null : x.users)), rate_prev: s.steps.map((x) => x.rate_prev) });
  }
  return out.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : a.bucket < b.bucket ? -1 : a.bucket > b.bucket ? 1 : 0));
}

export const pct = (v: number | null | undefined, digits = 1): string => (v === null || v === undefined ? '—' : `${(v * 100).toFixed(digits)}%`);

/** 詳細數據 CSV（UTF-8 BOM）：步驟表或趨勢表都能用。 */
export function funnelCsv(head: string[], body: (string | number | null)[][]): string {
  const esc = (v: string | number | null) => { const s = v === null ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  return '﻿' + [head.map(esc).join(','), ...body.map((r) => r.map(esc).join(','))].join('\r\n');
}
