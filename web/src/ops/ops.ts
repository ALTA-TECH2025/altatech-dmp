// 資料流與效能純函式（有單元測試）：狀態文字與顏色、數字格式、告警文字、SLA 判定
import type { Alert, AlertRule, ModulePerf, OpsStatus, Severity } from './types';

export const STATUS_CN: Record<OpsStatus, string> = { ok: '正常', warn: '注意', error: '異常', unknown: '未知' };
export const STATUS_PILL: Record<OpsStatus, string> = { ok: 'blue', warn: 'amber', error: 'red', unknown: 'gray' };
export const SEV_CN: Record<Severity, string> = { low: '低', medium: '中', high: '高', critical: '緊急' };
export const SEV_PILL: Record<Severity, string> = { low: 'gray', medium: 'amber', high: 'red', critical: 'red' };
export const JOB_STATUS_CN: Record<string, string> = { success: '成功', running: '執行中', retry_wait: '等待重試', retried: '已重試', failed: '失敗', paused: '已暫停', skipped: '略過' };

/** 毫秒 → 可讀：< 1 秒顯示 ms，否則秒（最多 1 位小數），≥ 60 秒顯示分鐘。 */
export function fmtMs(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return '—';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  return `${(ms / 60_000).toFixed(1)} 分`;
}
export function fmtBytes(b: number | null | undefined): string {
  if (b == null) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; let v = b;
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return `${i === 0 ? v : v.toFixed(v < 10 ? 2 : 1)} ${u[i]}`;
}
export function fmtRows(n: number | null | undefined): string {
  if (n == null) return '—';
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)} 億`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(n >= 1e6 ? 0 : 1)} 萬`;
  return n.toLocaleString();
}
export function fmtPct(x: number | null | undefined, d = 1): string { return x == null ? '—' : `${(x * 100).toFixed(d)}%`; }
export function fmtTs(s: string | null | undefined): string { return s ? s.replace('T', ' ').slice(0, 16) : '—'; }
/** 分鐘 → 「12 分鐘」「3.5 小時」「2.1 天」 */
export function lagText(min: number | null | undefined): string {
  if (min == null) return '—';
  if (min < 60) return `${Math.round(min)} 分鐘`;
  if (min < 24 * 60) return `${(min / 60).toFixed(1)} 小時`;
  return `${(min / 1440).toFixed(1)} 天`;
}

/** P95 對 SLA 的顏色：沒有 SLA 或沒有數據 → 灰；≤ SLA → 正常；≤ 1.5 倍 → 注意；否則異常 */
export function slaStatus(m: Pick<ModulePerf, 'p95_ms' | 'sla_ms'>): OpsStatus {
  if (m.p95_ms == null || m.sla_ms == null) return 'unknown';
  if (m.p95_ms <= m.sla_ms) return 'ok';
  return m.p95_ms <= m.sla_ms * 1.5 ? 'warn' : 'error';
}

/** 告警一行文字：「來源延遲 · 交易委託：240 min（門檻 > 120 min）」 */
export function alertText(a: Alert): string {
  const v = a.value == null ? '—' : a.unit === '比例' ? fmtPct(a.value) : a.unit === '倍 SLA' ? `${a.value.toFixed(2)} 倍` : `${Number.isInteger(a.value) ? a.value : a.value.toFixed(1)} ${a.unit}`;
  const t = a.unit === '比例' ? fmtPct(a.threshold) : a.unit === '倍 SLA' ? `${a.threshold} 倍` : `${a.threshold} ${a.unit}`;
  return `${a.cn} · ${a.subject}：${v}（門檻 ${a.op} ${t}）`;
}
export function ruleThreshold(r: AlertRule): string {
  return `${r.op} ${r.unit === '比例' ? fmtPct(r.threshold, 0) : r.unit === '倍 SLA' ? `${r.threshold} 倍 SLA` : `${r.threshold} ${r.unit}`}`;
}

/** 整體狀態：任一 error → error；任一 warn → warn；全 unknown → unknown；否則 ok */
export function overall(statuses: OpsStatus[]): OpsStatus {
  if (statuses.some((s) => s === 'error')) return 'error';
  if (statuses.some((s) => s === 'warn')) return 'warn';
  if (statuses.length && statuses.every((s) => s === 'unknown')) return 'unknown';
  return 'ok';
}

/** 告警依嚴重度排序後分組計數 */
export function countBySeverity(alerts: Alert[]): Partial<Record<Severity, number>> {
  const out: Partial<Record<Severity, number>> = {};
  for (const a of alerts) out[a.severity] = (out[a.severity] ?? 0) + 1;
  return out;
}
