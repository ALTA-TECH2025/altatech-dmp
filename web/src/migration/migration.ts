// 神策遷移純函式（有單元測試）：狀態文字、時序狀態、對帳判定文字
import type { ExportJob, InventoryItem } from './types';

export const INV_STATUS: Record<InventoryItem['status'], { cn: string; pill: string }> = { done: { cn: '完成', pill: 'blue' }, planned: { cn: '待盤點', pill: 'gray' }, inventoried: { cn: '已盤點', pill: 'blue' }, ok: { cn: '載入中／完成', pill: 'blue' }, warn: { cn: '注意', pill: 'amber' }, running: { cn: '執行中', pill: 'amber' }, blocked: { cn: '待連線', pill: 'amber' }, failed: { cn: '失敗', pill: 'red' } };
export const JOB_STATUS: Record<ExportJob['status'], { cn: string; pill: string }> = { pending: { cn: '待執行', pill: 'gray' }, blocked: { cn: '待連線', pill: 'amber' }, failed: { cn: '失敗', pill: 'red' }, warn: { cn: '有差異', pill: 'amber' }, ok: { cn: '完成', pill: 'blue' }, running: { cn: '執行中', pill: 'amber' } };
export const KIND_PILL: Record<string, string> = { auto: 'blue', recommended: 'blue', custom: '', not_ga: 'gray', history_only: 'gray' };

/** 切換時序：依今天判定每個里程碑 done／now／todo（now = 第一個未過的）。 */
export function timelineStatus(items: { date: string }[], today: string): ('done' | 'now' | 'todo')[] {
  let nowSeen = false;
  return items.map((m) => { if (m.date < today) return 'done'; if (!nowSeen) { nowSeen = true; return 'now'; } return 'todo'; });
}

export function diffText(d: number | null | undefined): string { return d === null || d === undefined ? '—' : `${d >= 0 ? '+' : ''}${(d * 100).toFixed(1)}%`; }
export function daysBetween(a: string, b: string): number { return Math.round((Date.parse(b) - Date.parse(a)) / 86400000) + 1; }
