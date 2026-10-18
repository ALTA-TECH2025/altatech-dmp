// 神策遷移純函式（有單元測試）：狀態文字、時序狀態、對帳判定文字
import type { ExportJob, InventoryItem, SensorsGroupStatus } from './types';

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

export const GROUP_STATUS: Record<SensorsGroupStatus, { cn: string; pill: string }> = { pending: { cn: '待處理', pill: 'amber' }, snapshot: { cn: '快照', pill: 'blue' }, rebuilt: { cn: '已重建', pill: 'blue' }, skipped: { cn: '略過', pill: 'gray' } };
/** 人數差異超過門檻（預設 5%）要人工確認；快照會因為沒對到用戶表的人而略少。 */
export function diffOk(d: number | null | undefined, thr = 0.05): boolean | null { return d === null || d === undefined ? null : Math.abs(d) <= thr; }
