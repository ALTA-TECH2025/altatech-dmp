// 儀表板純函式（有單元測試）：預設區間文字、事件卡片的指標別名／標籤、卡片預設標題、漏斗列挑第一組、表格欄位
import type { CardKind, Preset } from './types';

export const PRESET_CN: Record<Preset, string> = { '7d': '最近 7 天', '14d': '最近 14 天', '30d': '最近 30 天', '90d': '最近 90 天' };
export const KIND_CN: Record<CardKind, string> = { events: '事件分析', funnel: '漏斗分析', retention: '留存分析', path: '行為路徑', attribution: '歸因分析' };
export const AGG_CN: Record<string, string> = { total: '總次數', users: '觸發用戶數', avg_per_user: '人均次數', uniq: '去重數', sum: '數值求和', avg: '數值均值', avg_val: '數值人均值', max: '最大值', min: '最小值', p25: '下四分位', p50: '中位數', p75: '上四分位', p90: 'P90', mau: 'MAU（按天）' };

interface Metric { event?: string | null; agg: string; field?: string; alias?: string }

/** 事件卡片：查詢的指標 → 結果欄位別名（m1…）與人看的標籤（與事件分析頁同一規則）。 */
export function metricLabels(query: Record<string, unknown>): { aliases: string[]; labels: Record<string, string> } {
  const metrics = (Array.isArray(query.metrics) ? query.metrics : []) as Metric[];
  const aliases = metrics.map((m, i) => m.alias || `m${i + 1}`);
  const labels = Object.fromEntries(metrics.map((m, i) => [m.alias || `m${i + 1}`, `${m.event || '任一事件'} ${AGG_CN[m.agg] ?? m.agg}${m.field ? `(${m.field})` : ''}`]));
  return { aliases, labels };
}

/** 存為卡片時的預設標題：依查詢摘要產生，使用者可改。 */
export function defaultTitle(kind: CardKind, query: Record<string, unknown>): string {
  const ev = (x: unknown): string => (x && typeof x === 'object' && 'event' in x ? String((x as { event: unknown }).event ?? '') : '');
  if (kind === 'events') { const { labels, aliases } = metricLabels(query); return aliases.map((a) => labels[a]).join('、').slice(0, 60) || '事件分析'; }
  if (kind === 'funnel') return ((query.steps as { event: string; alias?: string }[] | undefined) ?? []).map((s) => s.alias || s.event).join(' → ').slice(0, 60) || '漏斗';
  if (kind === 'retention') return `${ev(query.initial) || '初始'} 留存${query.unit === 'week' ? '（週）' : query.unit === 'month' ? '（月）' : ''}`;
  if (kind === 'path') return `${ev(query.anchor) || '路徑'} ${query.direction === 'before' ? '之前' : '之後'} ${String(query.steps ?? 3)} 步`;
  return `${ev(query.target) || '轉化'} 歸因（${((query.models as string[] | undefined) ?? []).join('／')}）`;
}

/** 漏斗卡片只畫第一個（bucket, 分組）組合；多組請看漏斗分析頁。 */
export function firstGroup<T extends { bucket?: string; g1?: string | null }>(rows: T[]): { key: string; rows: T[]; groups: number } {
  const keys = new Map<string, T[]>();
  for (const r of rows) { const k = `${r.bucket ?? ''}|${r.g1 ?? ''}`; keys.set(k, [...(keys.get(k) ?? []), r]); }
  const first = keys.entries().next().value as [string, T[]] | undefined;
  return { key: first ? first[0].replace('|', ' ').trim() : '', rows: first ? first[1] : [], groups: keys.size };
}

/** 表格呈現用的欄位：去掉內部欄（_k、suppressed）。 */
export const tableColumns = (columns: string[] | undefined, rows: Record<string, unknown>[]): string[] => (columns ?? Object.keys(rows[0] ?? {})).filter((c) => c !== '_k' && c !== 'suppressed');

/** 卡片時間說明：釘住 → 顯示區間；否則跟儀表板走。 */
export function timeText(t: { from?: string; to?: string; pinned?: boolean } | undefined, preset: Preset): string {
  if (!t?.from || !t.to) return PRESET_CN[preset];
  return t.pinned ? `${t.from} ～ ${t.to}（釘住）` : `${PRESET_CN[preset]}：${t.from} ～ ${t.to}`;
}
