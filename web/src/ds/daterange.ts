// 快捷日期區間（純函式，有單元測試）：今天／昨天／本週／上週／本月／上月／今年／去年／過去 N 天（到昨天為止，與看板 preset 同口徑）。
export interface DateRange { from: string; to: string }
export type PresetKey = 'today' | 'yesterday' | 'this_week' | 'last_week' | 'this_month' | 'last_month' | 'this_year' | 'last_year' | '5d' | '7d' | '14d' | '30d' | '60d' | '90d' | '180d';
export const PRESET_CN: Record<PresetKey, string> = {
  today: '今天', yesterday: '昨天', this_week: '本週', last_week: '上週', this_month: '本月', last_month: '上月', this_year: '今年', last_year: '去年',
  '5d': '過去 5 天', '7d': '過去 7 天', '14d': '過去 14 天', '30d': '過去 30 天', '60d': '過去 60 天', '90d': '過去 90 天', '180d': '過去 180 天',
};
export const PRESET_GROUPS: PresetKey[][] = [['today', 'yesterday', 'this_week', 'last_week', 'this_month', 'last_month', 'this_year', 'last_year'], ['5d', '7d', '14d', '30d', '60d', '90d', '180d']];

/** 本地日期 → YYYY-MM-DD（不用 toISOString，避免時區跨日）。 */
export function isoDate(d: Date): string { return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; }
export function addDays(d: Date, n: number): Date { const x = new Date(d.getFullYear(), d.getMonth(), d.getDate()); x.setDate(x.getDate() + n); return x; }
const parse = (s: string): Date => { const [y, m, d] = s.split('-').map(Number); return new Date(y, m - 1, d); };

/** 快捷鍵 → 區間。過去 N 天 = 到昨天為止的 N 天（避免當天不完整）；週一起算。 */
export function presetRange(key: PresetKey, today: Date = new Date()): DateRange {
  const t = addDays(today, 0); const y = addDays(t, -1);
  const r = (a: Date, b: Date): DateRange => ({ from: isoDate(a), to: isoDate(b) });
  const dow = (t.getDay() + 6) % 7;                                           // 週一 = 0
  if (key === 'today') return r(t, t);
  if (key === 'yesterday') return r(y, y);
  if (key === 'this_week') return r(addDays(t, -dow), t);
  if (key === 'last_week') return r(addDays(t, -dow - 7), addDays(t, -dow - 1));
  if (key === 'this_month') return r(new Date(t.getFullYear(), t.getMonth(), 1), t);
  if (key === 'last_month') return r(new Date(t.getFullYear(), t.getMonth() - 1, 1), new Date(t.getFullYear(), t.getMonth(), 0));
  if (key === 'this_year') return r(new Date(t.getFullYear(), 0, 1), t);
  if (key === 'last_year') return r(new Date(t.getFullYear() - 1, 0, 1), new Date(t.getFullYear() - 1, 11, 31));
  const n = Number(key.slice(0, -1));
  return r(addDays(y, -(n - 1)), y);
}

/** 區間剛好等於哪個快捷鍵（顯示用）；都不是 → null（自訂）。 */
export function detectPreset(range: DateRange, today: Date = new Date()): PresetKey | null {
  for (const g of PRESET_GROUPS) for (const k of g) { const p = presetRange(k, today); if (p.from === range.from && p.to === range.to) return k; }
  return null;
}

export const fmtRange = (r: DateRange): string => (r.from && r.to ? `${r.from.replace(/-/g, '.')} - ${r.to.replace(/-/g, '.')}` : '選日期…');
export const rangeDays = (r: DateRange): number => (r.from && r.to ? Math.round((parse(r.to).getTime() - parse(r.from).getTime()) / 86_400_000) + 1 : 0);
/** 上一期：同長度、緊接在前。 */
export function previousPeriod(r: DateRange): DateRange { const n = rangeDays(r); return { from: isoDate(addDays(parse(r.from), -n)), to: isoDate(addDays(parse(r.from), -1)) }; }
