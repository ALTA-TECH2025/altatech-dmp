// 分群狀態與可做的動作（依身分 grants）；純函式，有單元測試。
import type { Action, Module } from '@/api/types';
import type { Segment, SegmentStatus } from '@/users/types';

export const STATUS_CN: Record<SegmentStatus, string> = { draft: '草稿', pending_approval: '待審批', active: '生效中', paused: '已暫停', recycled: '回收站' };
export const STATUS_PILL: Record<SegmentStatus, string> = { draft: 'gray', pending_approval: 'amber', active: 'blue', paused: 'gray', recycled: 'red' };
export const FREQ_CN = { daily: '每天', weekly: '每週一', monthly: '每月 1 日' } as const;
export const TRIGGER_CN = { manual: '手動', routine: '例行', backfill: '回溯' } as const;
export type SegAction = 'edit' | 'compute' | 'submit' | 'approve' | 'pause' | 'resume' | 'copy' | 'recycle' | 'restore' | 'purge';
export const ACTION_CN: Record<SegAction, string> = { edit: '編輯', compute: '立即計算', submit: '送審', approve: '審批', pause: '暫停', resume: '啟動', copy: '複製', recycle: '移入回收站', restore: '恢復', purge: '徹底刪除' };

export function actionsFor(seg: Pick<Segment, 'status' | 'owner'>, can: (m: Module, a: Action) => boolean, me?: string): SegAction[] {
  const use = can('segments', 'use'); const out: SegAction[] = [];
  if (seg.status === 'recycled') { if (use) out.push('restore'); if (can('segments', 'admin')) out.push('purge'); return out; }
  if (use) out.push('edit');
  if (seg.status === 'active' && use) out.push('compute', 'pause');
  if (seg.status === 'paused' && use) out.push('resume');
  if (seg.status === 'draft' && use) out.push('submit');
  if (seg.status === 'pending_approval' && can('segments', 'approve') && (seg.owner !== me || can('segments', 'admin'))) out.push('approve');
  if (use) out.push('copy', 'recycle');
  return out;
}

export function updateText(u: Segment['update'], tz: string): string {
  return u.mode === 'manual' ? '手動（1 版）' : `例行 ${FREQ_CN[u.freq]} ${u.earliest}（${tz}）· 留 ${u.keep} 版`;
}
