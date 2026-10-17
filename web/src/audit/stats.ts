// 日誌統計看板的純函式：把 /v1/audit/stats 的 daily 轉成 LineChart 的 Series、把 by_module 轉成長條、嚴重度排序。
import type { AuditStats } from '@/api/types';
import type { Series } from '@/analytics/series';

export const DAILY_KEYS: { key: keyof AuditStats['daily'][number]; cn: string }[] = [
  { key: 'logins', cn: '登入' }, { key: 'login_failed', cn: '登入失敗' }, { key: 'denied', cn: '權限拒絕' }, { key: 'profile_views', cn: '細查' }, { key: 'exports', cn: '匯出' }, { key: 'queries', cn: '查詢' },
];

export function dailySeries(st: Pick<AuditStats, 'daily'>, keys: (keyof AuditStats['daily'][number])[]): Series[] {
  return keys.map((k) => ({ key: String(k), label: DAILY_KEYS.find((d) => d.key === k)?.cn ?? String(k), metric: String(k), group: '', points: st.daily.map((d) => ({ x: d.date, y: Number(d[k] ?? 0), suppressed: false })) }));
}

export function hbars(rows: { label: string; n: number }[]): { label: string; n: number; pct: number }[] {
  const max = Math.max(1, ...rows.map((r) => r.n));
  return rows.map((r) => ({ ...r, pct: Math.round((r.n / max) * 100) }));
}

const SEV = { high: 0, medium: 1, low: 2 } as const;
export function sortAnomalies<T extends { severity: 'high' | 'medium' | 'low'; bucket: string }>(rows: T[]): T[] {
  return [...rows].sort((a, b) => SEV[a.severity] - SEV[b.severity] || b.bucket.localeCompare(a.bucket));
}

export function ruleText(r: AuditStats['rules'][number]): string {
  const win = r.window === 'hour' ? '1 小時' : r.window === 'day' ? '1 日' : '';
  if (r.field) return `${r.cn}：單筆 ${r.field} > ${r.threshold.toLocaleString()}`;
  if (r.distinct) return `${r.cn}：${win}內 ${r.distinct} ≥ ${r.threshold}`;
  return `${r.cn}：${r.off_hours_only ? '非上班時間 ' : ''}${win}內 > ${r.threshold}／${r.per === 'user' ? '人' : r.per ?? ''}`;
}
