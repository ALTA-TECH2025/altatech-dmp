// 事件分析頁的純函式（有單元測試）：數值格式、公式檢查、明細表的轉置／增長％／佔比％、圖型可用性。
import type { Series } from './series';
import { fmtNum } from './series';
import type { EventsRow } from './types';

export type NumFormat = 'auto' | 'int' | 'dec2' | 'pct0' | 'pct2' | 'twd0' | 'twd2';
export const FORMAT_CN: [NumFormat, string][] = [['auto', '數值（自動縮寫）'], ['int', '數值（取整）'], ['dec2', '數值（2 位小數）'], ['pct0', '百分比（取整）'], ['pct2', '百分比（2 位小數）'], ['twd0', '貨幣 NT$（取整）'], ['twd2', '貨幣 NT$（2 位小數）']];
export type ChartKind = 'line' | 'area' | 'bar' | 'stacked' | 'pie' | 'percent' | 'table';
export const CHART_CN: [ChartKind, string][] = [['line', '折線'], ['area', '面積'], ['bar', '長條'], ['stacked', '堆疊'], ['pie', '圓餅'], ['percent', '百分比堆疊'], ['table', '表格']];

/** 依格式顯示數值；遮罩分桶字串（≥5000000）原樣；null → —。百分比格式視值為「已經是百分比」（公式 m1/m2*100）。 */
export function fmtValue(v: unknown, f: NumFormat = 'auto'): string {
  if (v === null || v === undefined) return '—';
  if (typeof v !== 'number') return String(v);
  if (!Number.isFinite(v)) return '—';
  if (f === 'auto') return fmtNum(v);
  if (f === 'int') return Math.round(v).toLocaleString();
  if (f === 'dec2') return v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (f === 'pct0') return `${Math.round(v).toLocaleString()}%`;
  if (f === 'pct2') return `${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`;
  if (f === 'twd0') return `NT$ ${Math.round(v).toLocaleString()}`;
  return `NT$ ${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** 公式只能含指標別名、數字與 + - * / ( )，別名要存在（與後端 parse_formula 同規則）；回錯誤訊息或 null。 */
export function formulaError(expr: string, aliases: string[]): string | null {
  const toks = expr.match(/\d+(?:\.\d+)?|[A-Za-z_][A-Za-z0-9_]*|[-+*/()]/g) ?? [];
  if (!expr.trim()) return '公式不能是空的';
  if (toks.join('') !== expr.replace(/\s+/g, '')) return '只能含指標別名、數字與 + - * / ( )';
  const bad = toks.find((t) => /^[A-Za-z_]/.test(t) && !aliases.includes(t));
  if (bad) return `沒有這個指標別名：${bad}`;
  let wantOperand = true; let depth = 0;                               // 運算元／運算子要交替，括號要成對
  for (const t of toks) {
    if (wantOperand) { if (t === '(') { depth++; continue; } if (/^[-+*/)]$/.test(t)) return `公式寫法有誤：${t} 前面少了數值或指標`; wantOperand = false; }
    else { if (t === ')') { if (--depth < 0) return '括號不成對'; continue; } if (!/^[-+*/]$/.test(t)) return `公式寫法有誤：${t} 前面少了運算子`; wantOperand = true; }
  }
  return wantOperand || depth ? '公式沒有寫完或括號不成對' : null;
}
export const aliasOk = (a: string): boolean => /^[A-Za-z_][A-Za-z0-9_]*$/.test(a);

/** 增長％：有比較期（列上有 _prev 欄）就只對上期，上期缺值回 null；沒有比較期才對同一序列的上一個時間桶。回每列每指標的百分比（null = 算不出）。 */
export function growthOf(rows: EventsRow[], aliases: string[], hasBucket: boolean): (number | null)[][] {
  const last = new Map<string, number>();
  return rows.map((r) => aliases.map((a) => {
    const v = r[a]; if (typeof v !== 'number') return null;
    if (`${a}_prev` in r) { const prev = r[`${a}_prev`]; return typeof prev === 'number' && prev ? ((v - prev) / Math.abs(prev)) * 100 : null; }   // 有比較模式：缺上期值就是算不出，不拿上一桶充數
    if (!hasBucket) return null;
    const key = `${a}|${['cohort', 'g1', 'g2', 'g3'].map((g) => r[g] ?? '').join('|')}`;
    const p = last.get(key); last.set(key, v);
    return p === undefined || p === 0 ? null : ((v - p) / Math.abs(p)) * 100;
  }));
}
/** 佔比％：有分組時 = 佔同一時間桶（或合計）內所有分組的比例；沒分組時 = 佔整段期間的比例。 */
export function shareOf(rows: EventsRow[], aliases: string[], grouped: boolean): (number | null)[][] {
  const tot = new Map<string, number>();
  const key = (r: EventsRow, a: string) => `${a}|${grouped ? String(r.bucket ?? '') : ''}|${String(r.cohort ?? '')}`;   // 對照組各自算佔比
  for (const r of rows) for (const a of aliases) { const v = r[a]; if (typeof v === 'number') tot.set(key(r, a), (tot.get(key(r, a)) ?? 0) + v); }
  return rows.map((r) => aliases.map((a) => { const v = r[a]; const t = tot.get(key(r, a)); return typeof v === 'number' && t ? (v / t) * 100 : null; }));
}
/** 行列轉置：原本每列一個時間桶／分組 → 每列一個指標，欄是時間桶／分組（最多 60 欄）。 */
export function transpose(rows: EventsRow[], aliases: string[], labels: Record<string, string>, rowLabel: (r: EventsRow) => string): { head: string[]; body: string[][]; cut: number } {
  const keep = rows.slice(0, 60);
  return { head: ['指標', ...keep.map(rowLabel)], body: aliases.map((a) => [labels[a] ?? a, ...keep.map((r) => (r.suppressed ? 'K' : typeof r[a] === 'number' ? String(r[a]) : String(r[a] ?? '')))]), cut: rows.length - keep.length };
}

/** 哪些圖型可用：合計粒度沒有時間軸 → 折線／面積不可；圓餅要有可切的片（多序列或類別軸）。 */
export function chartAvailable(kind: ChartKind, hasBucket: boolean, series: Series[]): boolean {
  if (kind === 'line' || kind === 'area') return hasBucket;
  if (kind === 'pie') return series.length > 1 || (!hasBucket && (series[0]?.points.length ?? 0) > 1);
  if (kind === 'stacked' || kind === 'percent') return series.length > 1;
  return true;
}
/** 圓餅切片：多序列 → 每序列加總；單序列 → 每個 x 一片。 */
export function pieSlices(series: Series[]): { name: string; value: number }[] {
  if (series.length > 1) return series.map((s) => ({ name: s.label, value: s.points.reduce((n, p) => n + (p.y ?? 0), 0) }));
  return (series[0]?.points ?? []).map((p) => ({ name: p.x, value: p.y ?? 0 }));
}
/** 圓餅最多畫 n 片：其餘合併成「其他」，總和不變、百分比分母仍是全部。 */
export function pieTop(slices: { name: string; value: number }[], n: number): { name: string; value: number }[] {
  if (slices.length <= n) return slices;
  const sorted = [...slices].sort((a, b) => b.value - a.value);
  const rest = sorted.slice(n - 1).reduce((s, x) => s + x.value, 0);
  return [...sorted.slice(0, n - 1), { name: `其他（${slices.length - n + 1} 類）`, value: rest }];
}
/** 明細表 CSV（UTF-8 BOM）：給「複製」用，上限 500 列。 */
export function tableCsv(head: string[], body: string[][]): string {
  const esc = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  return '﻿' + [head.map(esc).join(','), ...body.slice(0, 500).map((r) => r.map(esc).join(','))].join('\r\n');
}
