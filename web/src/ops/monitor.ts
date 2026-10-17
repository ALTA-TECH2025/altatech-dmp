// 系統監控頁純函式（有單元測試）：面板序列 → 圖表序列（單位換算）、stat 面板最新值與門檻狀態、單位顯示
import type { Series } from '@/analytics/series';
import type { OpsStatus } from './types';

export type PanelUnit = 'reqps' | 's' | 'ms' | 'percentunit' | 'm' | 'bytes' | 'short' | 'none';
export interface PanelSeries { name: string; points: { t: string; v: number | null }[] }
export interface Panel { id: number; key: string; cn: string; section: string; kind: 'timeseries' | 'stat'; unit: PanelUnit | string; expr: string; thresholds: number[]; series: PanelSeries[] }
export interface OpsSeries { hours: number; from: string; to: string; interval_s: number; points: number; grafana: { url: string | null; dashboard_uid: string }; sections: { key: string; cn: string }[]; panels: Panel[] }

/** 圖表上用的單位（與換算倍率）：比例 → %、bytes → MB、其餘原樣 */
export function displayUnit(unit: string): { label: string; scale: number } {
  switch (unit) {
    case 'percentunit': return { label: '%', scale: 100 };
    case 'bytes': return { label: 'MB', scale: 1 / 1e6 };
    case 'reqps': return { label: '/s', scale: 1 };
    case 's': return { label: '秒', scale: 1 };
    case 'ms': return { label: 'ms', scale: 1 };
    case 'm': return { label: '分鐘', scale: 1 };
    default: return { label: '', scale: 1 };
  }
}

/** ISO 時間 → 圖表 x 標籤（MM-DD HH:MM，以字串排序即時間排序；顯示用瀏覽器本地時間） */
export function xLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 面板序列 → LineChart 的 Series（套單位倍率；最多 8 條，其餘略過並回報） */
export function toChartSeries(p: Panel): { series: Series[]; dropped: number } {
  const { scale } = displayUnit(p.unit);
  const shown = p.series.slice(0, 8);
  return {
    series: shown.map((s) => ({ key: s.name, label: s.name, metric: p.key, group: '', points: s.points.map((q) => ({ x: xLabel(q.t), y: q.v == null ? null : round(q.v * scale), suppressed: false })) })),
    dropped: Math.max(0, p.series.length - shown.length),
  };
}

/** stat 面板：每條序列最後一個非空值 */
export function latestValues(p: Panel): { name: string; v: number | null }[] {
  return p.series.map((s) => ({ name: s.name, v: [...s.points].reverse().find((q) => q.v != null)?.v ?? null }));
}

/** 門檻狀態：thresholds [warn, bad] 或 [bad]；特例 [0, 1] 代表 0 = 異常、1 = 正常（布林量規） */
export function thresholdStatus(v: number | null, thresholds: number[]): OpsStatus {
  if (v == null) return 'unknown';
  if (thresholds.length === 0) return 'ok';
  if (thresholds.length === 2 && thresholds[0] === 0 && thresholds[1] === 1) return v >= 1 ? 'ok' : 'error';
  if (thresholds.length === 1) return v >= thresholds[0] ? 'error' : 'ok';
  return v >= thresholds[1] ? 'error' : v >= thresholds[0] ? 'warn' : 'ok';
}

/** stat 面板的數字格式（含單位）：秒 → 時分、比例 → %、bytes → 自適應 */
export function fmtStat(v: number | null, unit: string): string {
  if (v == null) return '—';
  if (unit === 'percentunit') return `${(v * 100).toFixed(v < 0.1 ? 2 : 1)}%`;
  if (unit === 's') return v >= 86400 ? `${(v / 86400).toFixed(1)} 天` : v >= 3600 ? `${(v / 3600).toFixed(1)} 小時` : v >= 60 ? `${Math.round(v / 60)} 分鐘` : `${round(v)} 秒`;
  if (unit === 'm') return v >= 1440 ? `${(v / 1440).toFixed(1)} 天` : v >= 60 ? `${(v / 60).toFixed(1)} 小時` : `${Math.round(v)} 分鐘`;
  if (unit === 'bytes') return v >= 1e12 ? `${(v / 1e12).toFixed(2)} TB` : v >= 1e9 ? `${(v / 1e9).toFixed(2)} GB` : v >= 1e6 ? `${(v / 1e6).toFixed(1)} MB` : `${Math.round(v / 1e3)} KB`;
  if (unit === 'reqps') return `${round(v)} /s`;
  return Number.isInteger(v) ? v.toLocaleString() : round(v).toLocaleString();
}

export function grafanaPanelUrl(base: string | null, uid: string, panelId: number, hours: number): string | null {
  return base ? `${base}/d/${uid}?viewPanel=${panelId}&from=now-${hours}h&to=now` : null;
}

function round(v: number): number { return Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 100) / 100; }
