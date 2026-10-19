// 事件分析結果（長表：bucket, g1..gn, m1.., f1.., _k, suppressed）→ 圖表序列。純函式，有單元測試。
import type { EventsResult, EventsRow } from './types';

export interface Series { key: string; label: string; metric: string; group: string; points: { x: string; y: number | null; suppressed: boolean }[] }

export const GROUP_COLS = ['g1', 'g2', 'g3'];

export function groupLabel(r: EventsRow): string {
  return GROUP_COLS.filter((g) => g in r).map((g) => (r[g] === null || r[g] === undefined || r[g] === '' ? '（未填）' : String(r[g]))).join(' · ');
}

/** 有時間軸：指標 × 分組值 → 一條序列，x = bucket（依時間排序）。
 *  合計粒度：一個指標一條序列，x = 分組值、照結果順序（Top N 由大到小），分組值放在類別軸而不是各自成一條序列。 */
export function toSeries(res: EventsResult, metricAliases: string[], labels: Record<string, string> = {}): Series[] {
  const out = new Map<string, Series>();
  const hasBucket = res.columns.includes('bucket');
  for (const r of res.rows) {
    const g = groupLabel(r);
    for (const m of metricAliases) {
      const key = hasBucket ? `${m}|${g}` : m;
      if (!out.has(key)) out.set(key, { key, label: hasBucket ? [labels[m] ?? m, g].filter(Boolean).join(' · ') : (labels[m] ?? m), metric: m, group: hasBucket ? g : '', points: [] });
      const v = r[m];
      const n = typeof v === 'number' ? v : v === null || v === undefined ? null : Number(v);
      out.get(key)!.points.push({ x: hasBucket ? String(r.bucket) : g || '合計', y: n === null || Number.isFinite(n) ? n : null, suppressed: Boolean(r.suppressed) });   // 遮罩後的分桶字串（≥5000000）畫不出數值 → 空點，請看表格
    }
  }
  if (hasBucket) for (const s of out.values()) s.points.sort((a, b) => (a.x < b.x ? -1 : a.x > b.x ? 1 : 0));
  return Array.from(out.values());
}

/** 固定順序的類別色（不循環）：超過 8 條序列折進「其他」由呼叫端處理；這裡只回 token 名。 */
export function seriesColor(i: number): string {
  return `var(--s${Math.min(i + 1, 8)})`;
}

export function fmtNum(v: number | string | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (typeof v !== 'number') return String(v);                 // 遮罩分桶字串（≥5000000）原樣顯示
  const abs = Math.abs(v);
  if (abs >= 1e8) return `${(v / 1e8).toFixed(2)} 億`;
  if (abs >= 1e4) return `${(v / 1e4).toFixed(abs >= 1e6 ? 0 : 1)} 萬`;
  return Number.isInteger(v) ? v.toLocaleString() : v.toFixed(digits || 2);
}

/** 線性刻度：給 ticks 數量，回 [min, max, ticks]。從 0 起算（計數型指標不截斷軸）。 */
export function niceScale(max: number, ticks = 4): { max: number; ticks: number[] } {
  if (max <= 0) return { max: 1, ticks: [0, 1] };
  const raw = max / ticks; const mag = 10 ** Math.floor(Math.log10(raw)); const norm = raw / mag;
  const step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
  const top = Math.ceil(max / step) * step;
  return { max: top, ticks: Array.from({ length: Math.round(top / step) + 1 }, (_, i) => i * step) };
}
